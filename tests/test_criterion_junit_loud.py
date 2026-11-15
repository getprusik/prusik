import pytest

from prusik import gate


def test_unreadable_criteria_raises_not_silent(tmp_path, monkeypatch):
    from prusik import schema
    (tmp_path / "briefs").mkdir()
    crit = tmp_path / "c.yaml"
    crit.write_text("criteria: []\n")
    monkeypatch.setattr(schema, "criteria_path_for_brief", lambda p: crit)

    def boom(_p):
        raise OSError("denied")
    monkeypatch.setattr(schema, "load_criteria", boom)
    with pytest.raises(ValueError, match="c.yaml"):
        gate._criterion_junit(tmp_path, "f", "C1")


def test_missing_criteria_is_none(tmp_path):
    assert gate._criterion_junit(tmp_path, "f", "C1") is None
