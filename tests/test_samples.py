import pytest

from services.samples import parse_sample_csv, validate_children


def test_split_validation_normalizes_and_rejects_duplicates():
    children = validate_children("X", [{"code": " Xa ", "description": " a "}, {"code": "Xb"}])
    assert children[0]["code"] == "Xa"
    with pytest.raises(ValueError):
        validate_children("X", [{"code": "Xa"}, {"code": "Xa"}])


def test_csv_preview_parser_reports_bad_rows_without_writing():
    rows, errors = parse_sample_csv(b"Code,Description,Type\n X1 ,desc,T\nX1,,\n,missing,\n")
    assert rows[0]["code"] == "X1"
    assert any("duplicitn" in error for error in errors)
    assert any("chyb" in error for error in errors)
