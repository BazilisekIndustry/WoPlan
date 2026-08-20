"""Printable PLIST generation from the same task records used by schedule views."""
from __future__ import annotations

from datetime import date
from io import BytesIO
from xml.sax.saxutils import escape

from services.views import relevant_date, visible_tasks


def _fmt(value: str | None) -> str:
    return date.fromisoformat(value).strftime("%d.%m.%Y") if value else "-"


def build_plist_pdf(project: dict, tasks: list[dict], created_on: date | None = None) -> bytes:
    """Create a compact, multi-page PDF suitable for printing and empty projects."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether
    except ImportError as error:  # a useful runtime error for deployments missing the optional renderer
        raise RuntimeError("Pro export PDF nainstalujte závislost reportlab.") from error

    created_on = created_on or date.today()
    items = visible_tasks(tasks)
    output = BytesIO()
    doc = SimpleDocTemplate(output, pagesize=landscape(A4), leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm, bottomMargin=12 * mm)
    styles = getSampleStyleSheet()
    title = ParagraphStyle("PlistTitle", parent=styles["Title"], fontSize=18, leading=22, textColor=colors.HexColor("#123047"))
    body = ParagraphStyle("PlistBody", parent=styles["BodyText"], fontSize=8, leading=10)
    small = ParagraphStyle("PlistSmall", parent=body, fontSize=7, leading=9)
    story = [Paragraph("Požadavkový PLIST", title), Spacer(1, 3 * mm)]
    story.append(Paragraph(f"<b>Projekt:</b> {escape(project.get('project_number') or '-') } - {escape(project.get('name') or '-') }", body))
    if project.get("description"):
        story.append(Paragraph(f"<b>Popis:</b> {escape(project['description'])}", body))
    story.append(Paragraph(f"<b>Vytvořeno:</b> {created_on.strftime('%d.%m.%Y')} &nbsp;&nbsp; <b>Plánované dokončení:</b> {_fmt(project.get('planned_end'))}", body))
    story += [Spacer(1, 5 * mm), Paragraph("Celkový harmonogram projektu", styles["Heading2"])]

    dates = [date.fromisoformat(task["planned_start"]) for task in items] + [date.fromisoformat(task["planned_end"]) for task in items]
    if dates:
        begin, finish = min(dates), max(dates)
        span = max((finish - begin).days, 1)
        gantt_rows = [[Paragraph("Úkol", small), Paragraph("Časová osa", small)]]
        for task in items:
            left = (date.fromisoformat(task["planned_start"]) - begin).days / span
            width = max((date.fromisoformat(task["planned_end"]) - date.fromisoformat(task["planned_start"])).days / span, 0.025)
            # ASCII keeps the default PDF fonts portable and avoids missing-glyph squares.
            bar = "&nbsp;" * int(left * 42) + '<font color="#207398">' + "=" * max(1, int(width * 42)) + "</font>"
            gantt_rows.append([Paragraph(escape(task["name"]), small), Paragraph(f"{bar} &nbsp; {_fmt(task['planned_start'])} - {_fmt(task['planned_end'])}", small)])
        gantt = Table(gantt_rows, colWidths=[65 * mm, 185 * mm], repeatRows=1)
        gantt.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8F1F5")), ("GRID", (0, 0), (-1, -1), .25, colors.HexColor("#CCD9DF")), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
        story += [Paragraph(f"Rozsah projektu: <b>{begin.strftime('%d.%m.%Y')} - {finish.strftime('%d.%m.%Y')}</b> ({(finish - begin).days + 1} kalendářních dnů)", body), Spacer(1, 2 * mm), gantt]
    else:
        story.append(Paragraph("Projekt zatím neobsahuje žádné aktivní úkoly; harmonogram proto nelze zobrazit.", body))

    story += [Spacer(1, 6 * mm), Paragraph("Chronologický seznam úkolů", styles["Heading2"])]
    if not items:
        story.append(Paragraph("Nejsou k dispozici žádné úkoly pro export.", body))
    else:
        rows = [[Paragraph(header, small) for header in ("#", "Úkol a popis", "Pracoviště", "Požadovaný termín", "ZT")]]
        for index, task in enumerate(items, start=1):
            description = escape(task.get("description") or "Bez popisu")
            rows.append([Paragraph(str(index), small), Paragraph(f"<b>{escape(task['name'])}</b><br/>{description}", small), Paragraph(escape((task.get("workplaces") or {}).get("name") or "Nepřiřazeno"), small), Paragraph(_fmt(task.get("requested_end")), small), Paragraph(str(task.get("zt_count", 0)), small)])
        table = Table(rows, colWidths=[10 * mm, 122 * mm, 45 * mm, 43 * mm, 12 * mm], repeatRows=1)
        table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#123047")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("GRID", (0, 0), (-1, -1), .25, colors.HexColor("#BBCBD3")), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F8F9")]), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
        story.append(table)
    doc.build(story)
    return output.getvalue()
