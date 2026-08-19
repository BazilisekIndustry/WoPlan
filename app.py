from datetime import date, datetime, timedelta
import logging
import streamlit as st
from models.domain import Dependency, Task, Workplace
from repositories.planner import create_dependency, create_project, create_task, create_workplace, list_all_workplaces, list_dependencies, list_projects, list_tasks, list_workplaces, set_workplace_active, update_task_metadata, update_task_status
from repositories.supabase_repo import client, update_schedule_atomically
from services.calendars import calculate_delay_workdays, calculate_task_end, next_working_day
from services.capacity import monthly_capacity_hours, planned_hours_in_month
from services.conflicts import conflicts
from services.scheduling import dependency_start, move_task, revise_task
from services.projects import current_project_end, project_deadline_delay

st.set_page_config(page_title="Project Planner", layout="wide")
logger = logging.getLogger(__name__)

def streamlit_secret(name: str) -> str | None:
    try:
        return st.secrets.get(name)
    except FileNotFoundError:
        return None

def fail(error):
    logger.exception("Supabase operation failed", exc_info=error)
    message = str(error).lower()
    if "stale_schedule" in message:
        st.error("Jiný uživatel mezitím plán změnil. Obnovte stránku a zkuste to znovu.")
    elif "row-level security" in message or "permission denied" in message:
        st.error("Supabase zápis odmítl. Zkontrolujte, že je váš profil v `public.profiles` opravdu ve roli `admin`.")
    elif "foreign key" in message:
        st.error("Vybraný projekt nebo pracoviště už není dostupné. Obnovte stránku a vyberte jej znovu.")
    elif "duplicate key" in message or "unique" in message:
        st.error("Záznam s touto hodnotou již existuje. Použijte jiný název nebo číslo projektu.")
    elif "check constraint" in message or "violates check" in message:
        st.error("Hodnoty neodpovídají pravidlům databáze. Zkontrolujte délku, ZT, hodiny a pracovní dny.")
    else:
        st.error("Změnu se nepodařilo uložit. Žádné změny nebyly provedeny.")
    # Database error detail is useful to an administrator and contains no credentials or traceback.
    if globals().get("role") == "admin":
        error_code = getattr(error, "code", None)
        error_message = getattr(error, "message", None) or str(error)
        with st.expander("Technický detail pro správce"):
            st.code(f"typ: {type(error).__name__}\nkód: {error_code or 'bez kódu'}\nzpráva: {error_message!r}")

def domain(workplace_rows, task_rows, dependency_rows):
    workplaces = {r["id"]: Workplace(r["id"], r["name"], float(r["hours_per_workday"]), frozenset(r["working_days"])) for r in workplace_rows}
    tasks = [Task(r["id"], r["project_id"], r["name"], r["workplace_id"], r["duration_workdays"], date.fromisoformat(r["planned_start"]), date.fromisoformat(r["planned_end"]), r["status"]) for r in task_rows]
    deps = [Dependency(r["predecessor_task_id"], r["successor_task_id"], r["offset_workdays"]) for r in dependency_rows]
    return workplaces, tasks, deps

st.title("PROJECT PLANNER")
try:
    db = client(
        access_token=st.session_state.get("access_token"),
        refresh_token=st.session_state.get("refresh_token"),
        url=streamlit_secret("SUPABASE_URL"),
        key=streamlit_secret("SUPABASE_ANON_KEY"),
    )
    session = db.auth.get_session()
except RuntimeError:
    st.info("Doplňte SUPABASE_URL a SUPABASE_ANON_KEY do `.env` nebo Streamlit secrets."); st.stop()
if not session:
    st.subheader("Přihlášení")
    with st.form("login"):
        email = st.text_input("E-mail"); password = st.text_input("Heslo", type="password")
        if st.form_submit_button("Přihlásit"):
            try:
                result = db.auth.sign_in_with_password({"email": email, "password": password}); st.session_state.access_token, st.session_state.refresh_token = result.session.access_token, result.session.refresh_token; st.rerun()
            except Exception: st.error("Přihlášení se nepodařilo.")
    st.stop()
try:
    role = db.table("profiles").select("role").eq("id", session.user.id).single().execute().data["role"]
    workplace_rows, projects, task_rows, dependency_rows = list_workplaces(db), list_projects(db), list_tasks(db), list_dependencies(db)
except Exception:
    st.error("Data se nepodařilo načíst. Zkontrolujte migrace, RLS a profil uživatele."); st.stop()
workplaces, tasks, dependencies = domain(workplace_rows, task_rows, dependency_rows); today = date.today()
conflict_list = conflicts(tasks); conflict_ids = {str(x.first_task_id) for x in conflict_list} | {str(x.second_task_id) for x in conflict_list}
st.sidebar.caption(f"Role: **{role}**")
navigation = ["Dashboard", "Krátkodobý HMG", "Dlouhodobý výhled", "Projekty"]
if role == "admin": navigation.append("Pracoviště")
page = st.sidebar.radio("Navigace", navigation)
if st.sidebar.button("Odhlásit"): db.auth.sign_out(); st.session_state.clear(); st.rerun()

if page == "Dashboard":
    delayed = [t for t in tasks if t.status not in {"completed", "cancelled"} and t.planned_end < today]
    a,b,c = st.columns(3); a.metric("Aktivní úlohy", sum(t.status in {"planned","in_progress"} for t in tasks)); b.metric("Zpožděné", len(delayed)); c.metric("Kolize", len(conflict_list))
    if delayed or conflict_list: st.warning(f"Dnešní problémy: {len(delayed)} zpožděných úloh, {len(conflict_list)} kolizí.")

elif page == "Krátkodobý HMG":
    st.header("Krátkodobý plán")
    monday = st.date_input("Týden od", today - timedelta(days=today.weekday())); monday -= timedelta(days=monday.weekday()); days = [monday + timedelta(days=i) for i in range(7)]
    project_filter = st.selectbox("Projekt", ["Vše"] + [p["project_number"] for p in projects]); workplace_filter = st.selectbox("Pracoviště", ["Vše"] + [w.name for w in workplaces.values()]); status_filter = st.selectbox("Stav", ["Vše", "planned", "in_progress", "completed"])
    header = st.columns([2.3]+[1]*7); header[0].markdown("**Pracoviště / úloha**")
    for cell, day in zip(header[1:], days): cell.markdown(f"**{day:%a %d}**" + (" ⛱️" if day.weekday() > 4 else ""))
    for row in task_rows:
        task_start, task_end = date.fromisoformat(row["planned_start"]), date.fromisoformat(row["planned_end"])
        if row["status"] == "cancelled" or task_start > days[-1] or task_end < days[0] or (project_filter != "Vše" and row["projects"]["project_number"] != project_filter) or (workplace_filter != "Vše" and row["workplaces"]["name"] != workplace_filter) or (status_filter != "Vše" and row["status"] != status_filter): continue
        cells = st.columns([2.3]+[1]*7); delay = calculate_delay_workdays(task_end, today, workplaces[row["workplace_id"]]) if row["status"] not in {"completed", "cancelled"} else 0
        cells[0].write(f"{row['workplaces']['name']}\n\n{row['projects']['project_number']} · {row['name']}" + (f" ⚠️ {delay}d" if delay else ""))
        for cell, day in zip(cells[1:], days): cell.markdown("🟥" if str(row["id"]) in conflict_ids and task_start <= day <= task_end else "🟦" if task_start <= day <= task_end else "")
        if role == "admin":
            action_a, action_b, action_c = st.columns([1, 1, 5])
            if row["status"] == "planned" and action_a.button("Zahájit", key=f"start-{row['id']}"):
                try: update_task_status(db, row["id"], {"status": "in_progress", "actual_start": datetime.now().astimezone().isoformat(), "updated_by": session.user.id}); st.rerun()
                except Exception as error: fail(error)
            if row["status"] == "in_progress" and action_b.button("Dokončit", key=f"complete-{row['id']}"):
                try: update_task_status(db, row["id"], {"status": "completed", "actual_end": datetime.now().astimezone().isoformat(), "updated_by": session.user.id}); st.rerun()
                except Exception as error: fail(error)
            with st.expander(f"Přesun: {row['name']}"):
                new_start = st.date_input("Nový start", task_start, key=f"start-{row['id']}")
                if new_start != task_start:
                    try:
                        proposal = move_task(tasks, dependencies, workplaces, row["id"], new_start); original = {t.id:t for t in tasks}; changed = [t for t in proposal if t.planned_start != original[t.id].planned_start]
                        st.dataframe([{"Úloha":t.name,"Původní start":original[t.id].planned_start,"Nový start":t.planned_start} for t in changed], hide_index=True)
                        if conflicts(proposal): st.warning("Náhled obsahuje kolize – můžete je vědomě ponechat.")
                        if st.button("Použít změnu", key=f"apply-{row['id']}"):
                            ids={str(t.id) for t in changed}; payload=[{"id":str(t.id),"planned_start":t.planned_start.isoformat(),"planned_end":t.planned_end.isoformat()} for t in changed]; expected={str(r["id"]):r["updated_at"] for r in task_rows if str(r["id"]) in ids}
                            update_schedule_atomically(db,payload,expected); st.success("Plán byl aktualizován."); st.rerun()
                    except Exception as error: fail(error)
            with st.expander(f"Detail / úprava: {row['name']}"):
                st.write(f"Projekt: **{row['projects']['project_number']} – {row['projects']['name']}**")
                st.write(f"Pracoviště: **{row['workplaces']['name']}** · plán: **{row['planned_start']} – {row['planned_end']}** · délka: **{row['duration_workdays']} pracovních dnů** · ZT: **{row['zt_count']}**")
                with st.form(f"metadata-{row['id']}"):
                    name = st.text_input("Název", row["name"]); zt = st.number_input("ZT", 0, value=int(row["zt_count"]))
                    if st.form_submit_button("Uložit název / ZT"):
                        try: update_task_metadata(db, row["id"], {"name": name, "zt_count": int(zt), "updated_by": session.user.id}); st.rerun()
                        except Exception as error: fail(error)
                st.caption("Změna délky nebo pracoviště ovlivní termíny a před potvrzením se zobrazí náhled.")
                revision_key = f"revision-{row['id']}"; work_names = {w.name: w.id for w in workplaces.values()}
                rev_start = st.date_input("Nový plánovaný start", task_start, key=f"rev-start-{row['id']}")
                rev_duration = st.number_input("Nová délka (pracovní dny)", 1, value=int(row["duration_workdays"]), key=f"rev-duration-{row['id']}")
                current_work_name = next(w.name for w in workplaces.values() if w.id == row["workplace_id"])
                rev_work_name = st.selectbox("Nové pracoviště", list(work_names), index=list(work_names).index(current_work_name), key=f"rev-work-{row['id']}")
                if st.button("Zobrazit náhled změny", key=f"preview-revision-{row['id']}"):
                    st.session_state[revision_key] = {"start": rev_start.isoformat(), "duration": int(rev_duration), "workplace_id": work_names[rev_work_name]}
                if revision_key in st.session_state:
                    revision = st.session_state[revision_key]
                    try:
                        proposal = revise_task(tasks, dependencies, workplaces, row["id"], revision["duration"], revision["workplace_id"], date.fromisoformat(revision["start"]))
                        original = {t.id: t for t in tasks}; changed = [t for t in proposal if t != original[t.id]]
                        st.dataframe([{"Úloha":t.name,"Původní konec":original[t.id].planned_end,"Nový konec":t.planned_end,"Pracoviště":next(w.name for w in workplaces.values() if w.id == t.workplace_id)} for t in changed], hide_index=True)
                        if conflicts(proposal): st.warning("Náhled obsahuje kolize. Konflikt lze po potvrzení vědomě ponechat.")
                        if st.button("Potvrdit změnu plánování", type="primary", key=f"apply-revision-{row['id']}"):
                            ids={str(t.id) for t in changed}; payload=[]
                            for t in changed:
                                item={"id":str(t.id),"planned_start":t.planned_start.isoformat(),"planned_end":t.planned_end.isoformat()}
                                if t.id == row["id"]: item.update({"duration_workdays":t.duration_workdays,"workplace_id":str(t.workplace_id)})
                                payload.append(item)
                            expected={str(r["id"]):r["updated_at"] for r in task_rows if str(r["id"]) in ids}
                            update_schedule_atomically(db,payload,expected); st.session_state.pop(revision_key, None); st.success("Změna byla atomicky uložena."); st.rerun()
                    except Exception as error: fail(error)

elif page == "Dlouhodobý výhled":
    st.header("Dlouhodobý výhled"); year=int(st.number_input("Rok",2000,2200,today.year)); head=st.columns(13); head[0].markdown("**Pracoviště**")
    for month in range(1,13): head[month].markdown(f"**{date(year,month,1):%b}**")
    for workplace in workplaces.values():
        cells=st.columns(13); cells[0].write(workplace.name)
        for month in range(1,13):
            capacity=monthly_capacity_hours(workplace,year,month); planned=planned_hours_in_month(tasks,workplace,year,month); usage=100*planned/capacity if capacity else 0; cells[month].markdown(f"{'🔴' if usage>110 else '🟠' if usage>100 else '🟡' if usage>=80 else '🟢'} {usage:.0f}%")
    st.caption("🟢 0–79 % · 🟡 80–100 % · 🟠 100–110 % · 🔴 více než 110 %")

elif page == "Projekty":
    st.header("Projekty")
    if role == "admin":
        with st.expander("+ Nový projekt"):
            with st.form("new-project"):
                number=st.text_input("Číslo projektu"); name=st.text_input("Název"); description=st.text_area("Popis"); deadline=st.date_input("Plánované dokončení",value=None)
                if st.form_submit_button("Vytvořit"):
                    try: create_project(db,{"project_number":number,"name":name,"description":description or None,"planned_end":deadline.isoformat() if deadline else None,"created_by":session.user.id,"updated_by":session.user.id}); st.rerun()
                    except Exception as error: fail(error)
        with st.expander("+ Nová úloha"):
            pmap={f"{p['project_number']} – {p['name']}":p["id"] for p in projects}; wmap={w.name:w.id for w in workplaces.values()}
            with st.form("new-task"):
                project=st.selectbox("Projekt",pmap); name=st.text_input("Název úlohy"); work=st.selectbox("Pracoviště",wmap); duration=st.number_input("Délka (pracovní dny)",1,value=1); zt=st.number_input("ZT",0,value=0); start=st.date_input("Začátek",today)
                predecessor_options = {"Bez závislosti": None, **{f"{t['projects']['project_number']} · {t['name']} ({t['planned_end']})": t["id"] for t in task_rows}}
                predecessor=st.selectbox("Navázat na", predecessor_options); offset=st.number_input("Odstup",0,value=3)
                if st.form_submit_button("Vytvořit úlohu"):
                    try:
                        wp=workplaces[wmap[work]]
                        if predecessor: start=dependency_start(next(t for t in tasks if t.id==predecessor),Dependency(predecessor,"new",int(offset)),wp)
                        start=next_working_day(start,wp)
                        created=create_task(db,{"project_id":pmap[project],"name":name,"workplace_id":wp.id,"duration_workdays":int(duration),"zt_count":int(zt),"planned_start":start.isoformat(),"planned_end":calculate_task_end(start,int(duration),wp).isoformat(),"created_by":session.user.id,"updated_by":session.user.id})
                        if predecessor: create_dependency(db,{"predecessor_task_id":predecessor,"successor_task_id":created["id"],"offset_workdays":int(offset),"created_by":session.user.id})
                        st.rerun()
                    except Exception as error: fail(error)
    st.dataframe([{"Číslo":p["project_number"],"Projekt":p["name"],"Stav":p["status"],"Termín":p["planned_end"]} for p in projects],hide_index=True,use_container_width=True)
    if projects:
        selected_number = st.selectbox("Detail projektu", [p["project_number"] for p in projects])
        selected = next(p for p in projects if p["project_number"] == selected_number)
        project_tasks = [t for t in tasks if t.project_id == selected["id"] and t.status != "cancelled"]
        st.subheader(f"{selected['project_number']} – {selected['name']}")
        if selected.get("description"): st.caption(selected["description"])
        current_end = current_project_end(project_tasks)
        x, y, z = st.columns(3); x.metric("Plánovaný termín", selected.get("planned_end") or "—"); y.metric("Aktuální konec", current_end.isoformat() if current_end else "—")
        if current_end and selected.get("planned_end"):
            deadline = date.fromisoformat(selected["planned_end"]); delay = project_deadline_delay(project_tasks, deadline, workplaces)
            z.metric("Zpoždění projektu", f"{delay} pracovních dnů" if delay else "V termínu")
        st.dataframe([{"Úloha":t.name,"Pracoviště":next(w.name for w in workplaces.values() if w.id == t.workplace_id),"Start":t.planned_start,"Konec":t.planned_end,"Stav":t.status} for t in project_tasks],hide_index=True,use_container_width=True)

else:
    # This route is added to navigation only for administrators. RLS remains the enforcement layer.
    st.header("Pracoviště")
    st.caption("Pracoviště se nikdy nemažou. Deaktivace je skryje z nových úloh, ale zachová historii a integritu dat.")
    try:
        all_workplaces = list_all_workplaces(db)
    except Exception as error:
        fail(error); st.stop()
    with st.expander("+ Přidat pracoviště", expanded=not all_workplaces):
        with st.form("new-workplace", clear_on_submit=True):
            name = st.text_input("Název *")
            description = st.text_area("Popis")
            hours = st.number_input("Hodin za pracovní den *", min_value=0.25, value=8.0, step=0.25)
            selected_days = st.multiselect("Pracovní dny *", ["Pondělí", "Úterý", "Středa", "Čtvrtek", "Pátek", "Sobota", "Neděle"], default=["Pondělí", "Úterý", "Středa", "Čtvrtek", "Pátek"])
            annual_capacity = st.number_input("Roční kapacita hodin (volitelné)", min_value=0.0, value=0.0, step=1.0)
            if st.form_submit_button("Přidat pracoviště", type="primary"):
                if not name.strip() or not selected_days:
                    st.error("Vyplňte název a alespoň jeden pracovní den.")
                else:
                    try:
                        day_values = {"Pondělí": 0, "Úterý": 1, "Středa": 2, "Čtvrtek": 3, "Pátek": 4, "Sobota": 5, "Neděle": 6}
                        create_workplace(db, {"name": name.strip(), "description": description.strip() or None, "hours_per_workday": hours, "working_days": [day_values[day] for day in selected_days], "annual_capacity_hours": annual_capacity or None, "active": True})
                        st.success("Pracoviště bylo přidáno."); st.rerun()
                    except Exception as error: fail(error)
    day_names = ["Po", "Út", "St", "Čt", "Pá", "So", "Ne"]
    st.subheader("Aktivní")
    active_rows = [workplace for workplace in all_workplaces if workplace["active"]]
    st.dataframe([{"Název": w["name"], "Popis": w["description"] or "", "Hodiny/den": w["hours_per_workday"], "Pracovní dny": ", ".join(day_names[day] for day in w["working_days"]), "Roční kapacita": w["annual_capacity_hours"] or "—"} for w in active_rows], hide_index=True, use_container_width=True)
    for workplace in active_rows:
        if st.button(f"Deaktivovat: {workplace['name']}", key=f"deactivate-{workplace['id']}"):
            try:
                set_workplace_active(db, workplace["id"], False)
                st.success(f"Pracoviště {workplace['name']} bylo deaktivováno."); st.rerun()
            except Exception as error: fail(error)
    inactive_rows = [workplace for workplace in all_workplaces if not workplace["active"]]
    if inactive_rows:
        st.subheader("Neaktivní")
        st.dataframe([{"Název": w["name"], "Popis": w["description"] or "", "Hodiny/den": w["hours_per_workday"], "Pracovní dny": ", ".join(day_names[day] for day in w["working_days"])} for w in inactive_rows], hide_index=True, use_container_width=True)
        for workplace in inactive_rows:
            if st.button(f"Znovu aktivovat: {workplace['name']}", key=f"activate-{workplace['id']}"):
                try:
                    set_workplace_active(db, workplace["id"], True)
                    st.success(f"Pracoviště {workplace['name']} je znovu aktivní."); st.rerun()
                except Exception as error: fail(error)
