from datetime import date, datetime, timedelta
import logging
import streamlit as st
from models.domain import Dependency, Task, Workplace
from repositories.planner import create_dependency, create_project, create_task, create_workplace, list_all_workplaces, list_dependencies, list_projects, list_tasks, list_workplaces, set_workplace_active, update_task_status
from repositories.supabase_repo import client, update_schedule_atomically
from services.calendars import calculate_delay_workdays, calculate_task_end, next_working_day
from services.capacity import monthly_capacity_hours, planned_hours_in_month
from services.conflicts import conflicts, first_available_start
from services.scheduling import dependency_start, move_task, revise_task
from services.projects import current_project_end, project_deadline_delay
from services.plist_pdf import build_plist_pdf
from services.views import group_by_project, group_by_workplace, visible_tasks

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

def render_task_editor(row: dict, *, key_prefix: str) -> None:
    """One complete task-edit workflow, backed by the schedule transaction RPC."""
    task = next(item for item in tasks if item.id == row["id"])
    project_options = {f"{p['project_number']} - {p['name']}": p["id"] for p in projects}
    workplace_options = {workplace.name: workplace.id for workplace in workplaces.values()}
    current_project = next((label for label, value in project_options.items() if value == row["project_id"]), None)
    current_workplace = next((label for label, value in workplace_options.items() if value == row["workplace_id"]), None)
    if not current_project or not current_workplace:
        st.warning("Úkol odkazuje na neaktivní nebo nedostupná data a nelze jej bezpečně upravit.")
        return
    with st.form(f"task-editor-{key_prefix}-{row['id']}"):
        left, right = st.columns(2)
        name = left.text_input("Název úkolu *", row["name"])
        project_label = left.selectbox("Projekt *", list(project_options), index=list(project_options).index(current_project))
        workplace_label = left.selectbox("Pracoviště *", list(workplace_options), index=list(workplace_options).index(current_workplace))
        duration = left.number_input("Délka (pracovní dny) *", min_value=1, value=int(row["duration_workdays"]))
        zt_count = right.number_input("Počet zkušebních těles", min_value=0, value=int(row["zt_count"]))
        planned_start = right.date_input("Plánovaný začátek *", date.fromisoformat(row["planned_start"]))
        requested_enabled = right.checkbox("Zadat požadovaný termín dokončení", value=bool(row.get("requested_end")))
        requested_end = right.date_input("Požadovaný termín dokončení", date.fromisoformat(row["requested_end"]) if row.get("requested_end") else date.fromisoformat(row["planned_end"]), disabled=not requested_enabled)
        description = st.text_area("Popis úkolu", row.get("description") or "")
        submitted = st.form_submit_button("Uložit úkol a aktualizovat plán", type="primary")
    if not submitted:
        return
    if not name.strip():
        st.error("Název úkolu je povinný.")
        return
    try:
        proposal = revise_task(tasks, dependencies, workplaces, row["id"], int(duration), workplace_options[workplace_label], planned_start)
        original = {item.id: item for item in tasks}
        changed = [item for item in proposal if item != original[item.id]]
        root = next(item for item in proposal if item.id == row["id"])
        payload = []
        for item in changed or [root]:
            values = {"id": str(item.id), "planned_start": item.planned_start.isoformat(), "planned_end": item.planned_end.isoformat()}
            if item.id == row["id"]:
                values.update({"duration_workdays": item.duration_workdays, "workplace_id": str(item.workplace_id), "project_id": str(project_options[project_label]), "name": name.strip(), "description": description.strip(), "requested_end": requested_end.isoformat() if requested_enabled else "", "zt_count": int(zt_count)})
            payload.append(values)
        expected = {str(item["id"]): item["updated_at"] for item in task_rows if str(item["id"]) in {entry["id"] for entry in payload}}
        update_schedule_atomically(db, payload, expected)
        st.success("Úkol i navazující harmonogram byly aktualizovány.")
        st.rerun()
    except Exception as error:
        fail(error)

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
    filtered = visible_tasks(task_rows, start=days[0], end=days[-1], project_id=next((p["id"] for p in projects if p["project_number"] == project_filter), None), workplace_id=next((w.id for w in workplaces.values() if w.name == workplace_filter), None), status=None if status_filter == "Vše" else status_filter)
    view = st.radio("Zobrazit", ["Podle pracovišť", "Podle projektů"], horizontal=True, label_visibility="collapsed")
    groups = group_by_workplace(filtered) if view == "Podle pracovišť" else group_by_project(filtered)
    if not groups:
        st.info("Ve zvoleném týdnu a filtrech nejsou žádné aktivní úkoly.")
    for group_name, group_tasks in groups:
        st.subheader(group_name)
        for row in group_tasks:
            task_start, task_end = date.fromisoformat(row["planned_start"]), date.fromisoformat(row["planned_end"])
            cells = st.columns([2.8] + [1] * 7)
            workplace_name = (row.get("workplaces") or {}).get("name") or "Nepřiřazeno"
            project_name = (row.get("projects") or {}).get("project_number") or "Bez projektu"
            delay = calculate_delay_workdays(task_end, today, workplaces[row["workplace_id"]]) if row["status"] not in {"completed", "cancelled"} else 0
            cells[0].markdown(f"**{row['name']}**  \n{project_name} · {workplace_name}" + (f" · ⚠️ {delay} prac. d" if delay else ""))
            for cell, day in zip(cells[1:], days): cell.markdown("🟥" if str(row["id"]) in conflict_ids and task_start <= day <= task_end else "🟦" if task_start <= day <= task_end else "")
            if role == "admin":
                action_a, action_b, action_c = st.columns([1, 1, 5])
                if row["status"] == "planned" and action_a.button("Zahájit", key=f"action-start-{row['id']}"):
                    try: update_task_status(db, row["id"], {"status": "in_progress", "actual_start": datetime.now().astimezone().isoformat(), "updated_by": session.user.id}); st.rerun()
                    except Exception as error: fail(error)
                if row["status"] == "in_progress" and action_b.button("Dokončit", key=f"complete-{row['id']}"):
                    try: update_task_status(db, row["id"], {"status": "completed", "actual_end": datetime.now().astimezone().isoformat(), "updated_by": session.user.id}); st.rerun()
                    except Exception as error: fail(error)
                with action_c.expander("Přesunout v čase"):
                    moving_task = next(task for task in tasks if task.id == row["id"])
                    suggested_start = first_available_start(moving_task, task_start, workplaces[row["workplace_id"]], tasks)
                    new_start = st.date_input("Nový start", suggested_start, key=f"move-date-{row['id']}")
                    if suggested_start != task_start: st.caption(f"První volný termín na pracovišti: {suggested_start:%d.%m.%Y}")
                    if new_start != task_start and st.button("Použít přesun", key=f"apply-{row['id']}"):
                        try:
                            proposal = move_task(tasks, dependencies, workplaces, row["id"], new_start)
                            original = {task.id: task for task in tasks}; changed = [task for task in proposal if task != original[task.id]]
                            if conflicts(proposal): st.warning("Přesun vytváří kolizi pracoviště; změnu můžete ověřit na červeně označených úkolech.")
                            payload = [{"id": str(task.id), "planned_start": task.planned_start.isoformat(), "planned_end": task.planned_end.isoformat()} for task in changed]
                            expected = {str(item["id"]): item["updated_at"] for item in task_rows if str(item["id"]) in {entry["id"] for entry in payload}}
                            update_schedule_atomically(db, payload, expected); st.success("Plán byl aktualizován."); st.rerun()
                        except Exception as error: fail(error)
        st.caption(f"{len(group_tasks)} úkolů")
    st.caption("🟦 plánovaný průběh · 🟥 kolize pracoviště · časový rozsah a filtry zůstávají stejné v obou pohledech")

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
                project=st.selectbox("Projekt",pmap); name=st.text_input("Název úlohy"); description=st.text_area("Popis úkolu"); work=st.selectbox("Pracoviště",wmap); duration=st.number_input("Délka (pracovní dny)",1,value=1); zt=st.number_input("Počet zkušebních těles",0,value=0); start=st.date_input("Začátek",today); requested_end=st.date_input("Požadovaný termín dokončení", value=None)
                predecessor_options = {"Bez závislosti": None, **{f"{t['projects']['project_number']} · {t['name']} ({t['planned_end']})": t["id"] for t in task_rows}}
                predecessor_label = st.selectbox("Navázat na", predecessor_options)
                predecessor = predecessor_options[predecessor_label]
                offset=st.number_input("Odstup",0,value=3)
                if st.form_submit_button("Vytvořit úlohu"):
                    try:
                        wp=workplaces[wmap[work]]
                        if predecessor: start=dependency_start(next(t for t in tasks if t.id==predecessor),Dependency(predecessor,"new",int(offset)),wp)
                        start=next_working_day(start,wp)
                        if not name.strip(): raise ValueError("Název úlohy je povinný.")
                        created=create_task(db,{"project_id":pmap[project],"name":name.strip(),"description":description.strip() or None,"workplace_id":wp.id,"duration_workdays":int(duration),"zt_count":int(zt),"requested_end":requested_end.isoformat() if requested_end else None,"planned_start":start.isoformat(),"planned_end":calculate_task_end(start,int(duration),wp).isoformat(),"created_by":session.user.id,"updated_by":session.user.id})
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
        project_rows = visible_tasks([row for row in task_rows if row["project_id"] == selected["id"]])
        st.dataframe([{"Úloha":row["name"],"Popis":row.get("description") or "—","Pracoviště":(row.get("workplaces") or {}).get("name") or "—","Start":row["planned_start"],"Konec":row["planned_end"],"Požadovaný termín":row.get("requested_end") or "—","ZT":row["zt_count"],"Stav":row["status"]} for row in project_rows],hide_index=True,use_container_width=True)
        if role == "admin":
            st.subheader("Upravit existující úkol")
            if project_rows:
                task_label = st.selectbox("Úkol k úpravě", [f"{row['name']} ({row['planned_start']})" for row in project_rows])
                editable = project_rows[[f"{row['name']} ({row['planned_start']})" for row in project_rows].index(task_label)]
                with st.expander("Detail a editace úkolu", expanded=True):
                    render_task_editor(editable, key_prefix="project")
            else:
                st.info("Projekt zatím nemá žádné úkoly k úpravě.")
        st.subheader("Požadavkový PLIST")
        st.caption("Export vždy vychází z aktuálních úkolů projektu a používá stejné chronologické řazení jako HMG.")
        try:
            pdf = build_plist_pdf(selected, project_rows)
            safe_number = "".join(char if char.isalnum() or char in "-_" else "_" for char in selected["project_number"])
            st.download_button("Stáhnout PLIST v PDF", pdf, file_name=f"PLIST_{safe_number}.pdf", mime="application/pdf", type="primary")
        except Exception as error:
            st.error("PDF se nepodařilo připravit.")
            if role == "admin": st.caption(str(error))

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
