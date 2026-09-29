"""Printable PLIST generation from the same task records used by schedule views."""
from __future__ import annotations

from datetime import date
from io import BytesIO
import os
from pathlib import Path
from xml.sax.saxutils import escape

from services.views import relevant_date, visible_tasks


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PROJECT_ROOT / "assets"
FONT_DIR = ASSETS_DIR / "fonts"
FONT_REGULAR = FONT_DIR / "DejaVuSansMono.ttf"
FONT_BOLD = FONT_DIR / "DejaVuSansMono-Bold.ttf"


def _register_unicode_fonts() -> tuple[str, str]:
    """Register embedded, project-bundled fonts; never depend on host fonts."""
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
    except ImportError as error:
        raise RuntimeError("Pro export PDF nainstalujte závislost reportlab.") from error
    if not FONT_REGULAR.exists() or not FONT_BOLD.exists():
        raise RuntimeError("V aplikaci chybí vložený Unicode font pro export PDF.")
    for name, path in (("PlistUnicode", FONT_REGULAR), ("PlistUnicode-Bold", FONT_BOLD)):
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(path), subfontIndex=0))
    pdfmetrics.registerFontFamily("PlistUnicode", normal="PlistUnicode", bold="PlistUnicode-Bold")
    return "PlistUnicode", "PlistUnicode-Bold"


def _logo_flowable(max_width: float, max_height: float):
    """Load a deployable logo from assets or an explicit environment path.

    Missing or invalid branding must never stop the operational PLIST export.
    """
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import Image
    candidates = ([Path(os.environ["PLIST_LOGO_PATH"])] if os.environ.get("PLIST_LOGO_PATH") else []) + [
        ASSETS_DIR / "logo.png", ASSETS_DIR / "logo.jpg", ASSETS_DIR / "logo.jpeg",
    ]
    for path in candidates:
        try:
            if not path.is_file():
                continue
            image = ImageReader(str(path))
            width, height = image.getSize()
            scale = min(max_width / width, max_height / height, 1)
            return Image(str(path), width=width * scale, height=height * scale)
        except Exception:
            continue
    # SVG works when svglib is installed; PNG/JPEG stay zero-dependency.
    svg_path = ASSETS_DIR / "logo.svg"
    if svg_path.is_file():
        try:
            from svglib.svglib import svg2rlg
            from reportlab.graphics.shapes import Drawing
            drawing = svg2rlg(str(svg_path))
            if drawing:
                scale = min(max_width / drawing.width, max_height / drawing.height, 1)
                drawing.scale(scale, scale)
                drawing.width *= scale; drawing.height *= scale
                return drawing
        except Exception:
            pass
    return None


def _fmt(value: str | None) -> str:
    return date.fromisoformat(value).strftime("%d.%m.%Y") if value else "-"


def build_plist_pdf(project: dict, tasks: list[dict], created_on: date | None = None, task_sample_codes: dict[str, list[str]] | None = None,
                    task_attachments: dict[str, list[dict]] | None = None,
                    attachment_images: dict[str, bytes] | None = None) -> bytes:
    """Create a compact, multi-page PDF suitable for printing and empty projects."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak, KeepTogether
        from reportlab.lib.utils import ImageReader
    except ImportError as error:  # a useful runtime error for deployments missing the optional renderer
        raise RuntimeError("Pro export PDF nainstalujte závislost reportlab.") from error

    created_on = created_on or date.today()
    items = visible_tasks(tasks)
    task_attachments = task_attachments or {}
    attachment_images = attachment_images or {}
    regular, bold = _register_unicode_fonts()
    output = BytesIO()
    doc = SimpleDocTemplate(output, pagesize=landscape(A4), leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm, bottomMargin=12 * mm)
    styles = getSampleStyleSheet()
    title = ParagraphStyle("PlistTitle", parent=styles["Title"], fontName=bold, fontSize=18, leading=22, textColor=colors.HexColor("#123047"))
    heading = ParagraphStyle("PlistHeading", parent=styles["Heading2"], fontName=bold, fontSize=12, leading=15, textColor=colors.HexColor("#123047"))
    body = ParagraphStyle("PlistBody", parent=styles["BodyText"], fontName=regular, fontSize=8, leading=10)
    warning = ParagraphStyle("PlistWarning", parent=body, fontName=bold, textColor=colors.HexColor("#B42318"), backColor=colors.HexColor("#FEE4E2"), borderColor=colors.HexColor("#B42318"), borderWidth=.5, borderPadding=5, spaceBefore=3, spaceAfter=3)
    small = ParagraphStyle("PlistSmall", parent=body, fontName=regular, fontSize=7, leading=9)
    small_bold = ParagraphStyle("PlistSmallBold", parent=small, fontName=bold)
    header_text = [Paragraph("Požadavkový list HK", title), Spacer(1, 1 * mm), Paragraph(f"<b>Projekt:</b> {escape(project.get('project_number') or '-') } - {escape(project.get('name') or '-') }", body)]
    logo = _logo_flowable(42 * mm, 18 * mm)
    header = Table([[logo or "", header_text]], colWidths=[46 * mm, 204 * mm], hAlign="LEFT")
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
    dates = [date.fromisoformat(task["planned_start"]) for task in items] + [date.fromisoformat(task["planned_end"]) for task in items]
    planned_project_end = date.fromisoformat(project["planned_end"]) if project.get("planned_end") else None
    schedule_finish = max(dates) if dates else None
    story = [header, Spacer(1, 2 * mm)]
    if project.get("description"):
        story.append(Paragraph(f"<b>Popis:</b> {escape(project['description'])}", body))
    story.append(Paragraph(f"<b>Vytvořeno:</b> {created_on.strftime('%d.%m.%Y')} &nbsp;&nbsp; <b>Plánované dokončení:</b> {_fmt(project.get('planned_end'))}", body))
    if planned_project_end and schedule_finish and schedule_finish > planned_project_end:
        story.append(Paragraph(f"⚠ HMG končí {schedule_finish:%d.%m.%Y}, což je po plánovaném dokončení projektu ({planned_project_end:%d.%m.%Y}).", warning))
    story += [Spacer(1, 5 * mm), Paragraph("Celkový harmonogram projektu", heading)]

    if dates:
        begin, finish = min(dates), max(dates)
        span = max((finish - begin).days, 1)
        gantt_rows = [[Paragraph("Úkol", small_bold), Paragraph("Časová osa", small_bold)]]
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

    story += [Spacer(1, 6 * mm), Paragraph("Chronologický seznam úkolů", heading)]
    if not items:
        story.append(Paragraph("Nejsou k dispozici žádné úkoly pro export.", body))
    else:
        task_sample_codes = task_sample_codes or {}
        rows = [[Paragraph(header, small_bold) for header in ("#", "Úkol a popis", "Vzorky", "Pracoviště", "Požadovaný termín", "ZT")]]
        for index, task in enumerate(items, start=1):
            description = escape(task.get("description") or "Bez popisu")
            codes = ", ".join(task_sample_codes.get(str(task["id"]), [])) or "—"
            task_files = task_attachments.get(str(task["id"]), [])
            appendix_label = f"Příloha č. {sum(bool(task_attachments.get(str(previous['id']), [])) for previous in items[:index])}" if task_files else "—"
            appendix_text = f"Přílohy - A - {appendix_label}" if task_files else "Přílohy - N -"
            rows.append([Paragraph(str(index), small), Paragraph(f"<b>{escape(task['name'])}</b><br/>{description}<br/><b>{appendix_text}</b>", small), Paragraph(escape(codes), small), Paragraph(escape((task.get("workplaces") or {}).get("name") or "Nepřiřazeno"), small), Paragraph(_fmt(task.get("requested_end")), small), Paragraph(str(task.get("zt_count", 0)), small)])
        table = Table(rows, colWidths=[9 * mm, 74 * mm, 84 * mm, 37 * mm, 37 * mm, 10 * mm], repeatRows=1)
        table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#60A2D4")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("GRID", (0, 0), (-1, -1), .25, colors.HexColor("#BBCBD3")), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F8F9")]), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
        story.append(table)
    appendix_number = 0
    for task in items:
        task_files = task_attachments.get(str(task["id"]), [])
        if not task_files:
            continue
        appendix_number += 1
        story.append(PageBreak())
        story.append(Paragraph(f"Příloha č. {appendix_number} – {escape(task['name'])}", heading))
        for attachment in task_files:
            image_data = attachment_images.get(str(attachment["id"]))
            if not image_data:
                continue
            try:
                reader = ImageReader(BytesIO(image_data))
                width, height = reader.getSize()
                scale = min(250 * mm / width, 165 * mm / height, 1)
                story.append(KeepTogether([Spacer(1, 3 * mm), Image(BytesIO(image_data), width=width * scale, height=height * scale)]))
            except Exception as error:
                raise RuntimeError(f"Obrázek {attachment.get('file_name', '')} se nepodařilo vložit do PDF.") from error
    doc.build(story)
    return output.getvalue()
