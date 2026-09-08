"""Pure sample validation and CSV parsing used by the Streamlit UI."""
from __future__ import annotations

import csv
from io import StringIO


VALID_STATUSES = {"active", "split", "inactive"}


def normalize_code(value: str | None) -> str:
    return (value or "").strip()


def validate_children(parent_code: str, children: list[dict], existing_codes: set[str] | None = None) -> list[dict]:
    """Return normalized children or raise a Czech UI-friendly validation error."""
    normalized = [{**child, "code": normalize_code(child.get("code")), "description": (child.get("description") or "").strip() or None,
                   "type": (child.get("type") or "").strip() or None} for child in children]
    if len(normalized) < 2:
        raise ValueError("Rozdělení musí vytvořit alespoň dva nové vzorky.")
    codes = [child["code"] for child in normalized]
    if not all(codes):
        raise ValueError("Kód každého nového vzorku je povinný.")
    if len(set(codes)) != len(codes):
        raise ValueError("Kódy nových vzorků se nesmí opakovat.")
    if parent_code in codes:
        raise ValueError("Nový vzorek nemůže mít stejný kód jako rodič.")
    if set(codes) & (existing_codes or set()):
        raise ValueError("Některý kód vzorku již existuje.")
    return normalized


def parse_sample_csv(content: bytes) -> tuple[list[dict], list[str]]:
    """Parse the deliberately small, easily replaceable Code/Description/Type mapping."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = content.decode("cp1250")
        except UnicodeDecodeError as error:
            raise ValueError("CSV nelze přečíst v UTF-8 ani Windows-1250.") from error
    reader = csv.DictReader(StringIO(text))
    if not reader.fieldnames:
        raise ValueError("CSV neobsahuje záhlaví.")
    headers = {name.strip().lower(): name for name in reader.fieldnames if name}
    if "code" not in headers:
        raise ValueError("CSV musí obsahovat sloupec Code.")
    rows, errors, seen = [], [], set()
    for line, source in enumerate(reader, start=2):
        if not any((value or "").strip() for value in source.values()):
            continue
        code = normalize_code(source.get(headers["code"]))
        if not code:
            errors.append(f"Řádek {line}: chybí Code.")
        elif code in seen:
            errors.append(f"Řádek {line}: duplicitní Code {code!r} v CSV.")
        seen.add(code)
        rows.append({"code": code, "description": (source.get(headers.get("description", "")) or "").strip() or None,
                     "type": (source.get(headers.get("type", "")) or "").strip() or None, "line": line})
    if not rows:
        errors.append("CSV neobsahuje žádné neprázdné řádky.")
    return rows, errors
