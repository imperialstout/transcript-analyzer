import importlib.util
import json
from datetime import date
from pathlib import Path

import pytest

from analyzer import brief_state as bs

_spec = importlib.util.spec_from_file_location(
    "brief_triage", Path(__file__).resolve().parent.parent / "bin" / "brief-triage.py"
)
bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bt)

TODAY = date(2026, 11, 12)  # Thursday


def _item(id_, type_, owner="Brad", deadline=None, inbox=True, notes=""):
    return {
        "id": id_, "text": f"item {id_}", "type": type_, "owner": owner, "deadline": deadline,
        "source_brief": "b", "source_pulse": None, "status": "pending", "created_at": "2026-11-01T00:00:00",
        "closed_at": None, "deferred_to": None, "notes": notes, "resurfaced_count": 0,
        "source_analyzed": "x.md" if inbox else None,
    }


@pytest.fixture
def state(tmp_path, monkeypatch):
    p = tmp_path / "brief-state.json"
    items = [
        _item("aaaaaa111111", "act"),
        _item("bbbbbb222222", "they-owe", owner="Colin", inbox=False, deadline="2026-11-10"),
        _item("cccccc333333", "they-owe", owner="Jitu"),
        _item("aaaaab444444", "watch"),
        _item("dddddd555555", "act", deadline="2026-11-15"),
    ]
    p.write_text(json.dumps(items))
    monkeypatch.setattr(bs, "_STATE_PATH", p)
    return p


def _get(state, prefix):
    return next(i for i in json.loads(state.read_text()) if i["id"].startswith(prefix))


def _backups(state):
    return list(state.parent.glob("brief-state.backup-*.json"))


@pytest.mark.parametrize("tok,expected", [
    ("a", ("a", None)), ("accept", ("a", None)), ("ACCEPT", ("a", None)),
    ("act", ("act", None)), ("act:2026-11-20", ("act", "2026-11-20")), ("act:tomorrow", ("act", "2026-11-13")),
    ("watch", ("watch", None)), ("delegate:colin", ("delegate", "colin")),
    ("d", ("d", None)), ("done", ("d", None)), ("complete", ("d", None)),
    ("x", ("x", None)), ("drop", ("x", None)), ("dismiss", ("x", None)),
    ("w2026-11-20", ("w", "2026-11-20")), ("defer:tomorrow", ("w", "2026-11-13")), ("w:+3d", ("w", "2026-11-15")),
    ("p", ("p", None)), ("pin", ("p", None)), ("u", ("u", None)), ("unpin", ("u", None)),
    ("k", ("k", None)), ("keep", ("k", None)),
])
def test_aliases(tok, expected):
    assert bt.normalize(tok, TODAY) == expected


@pytest.mark.parametrize("tok", ["w", "watch:foo", "w:", "defer:", "defer:yesterday-ish", "defer:2026-11-01", "delegate", "delegate:a b", "zzz"])
def test_bad_tokens(tok):
    with pytest.raises(ValueError):
        bt.normalize(tok, TODAY)


def test_w_error_explains_defer_vs_watch():
    with pytest.raises(ValueError, match="defer.*watch"):
        bt.normalize("w", TODAY)


@pytest.mark.parametrize("when,expected", [
    ("tomorrow", "2026-11-13"), ("wednesday", "2026-11-18"), ("thu", "2026-11-19"),
    ("+3d", "2026-11-15"), ("+2w", "2026-11-26"), ("2026-11-20", "2026-11-20"),
    ("nextweek", "2026-11-16"), ("today", "2026-11-12"),
])
def test_parse_when(when, expected):
    assert bt.parse_when(when, TODAY).isoformat() == expected


def test_delegate(state):
    bt.run_apply(["cccccc:delegate:colin"], TODAY)
    i = _get(state, "cccccc")
    assert i["type"] == "delegate" and i["owner"] == "colin" and i["source_analyzed"] is None
    assert "delegated from Jitu" in i["notes"]


def test_act_deadline(state):
    bt.run_apply(["aaaaaa1:act:2026-11-20"], TODAY)
    assert _get(state, "aaaaaa1")["deadline"] == "2026-11-20"
    bt.run_apply(["dddddd:act:2026-11-20"], TODAY)
    i = _get(state, "dddddd")
    assert i["deadline"] == "2026-11-20" and "2026-11-15" in i["notes"]


def test_bad_token_writes_nothing(state):
    before = state.read_text()
    with pytest.raises(ValueError):
        bt.run_apply(["aaaaaa1:x", "bbbbbb:bogus"], TODAY)
    assert state.read_text() == before and not _backups(state)


def test_duplicate_and_ambiguous(state):
    with pytest.raises(ValueError, match="same item"):
        bt.run_apply(["aaaaaa1:x", "aaaaaa11:d"], TODAY)
    with pytest.raises(ValueError, match="ambiguous"):
        bt.run_apply(["aaaaa:x"], TODAY)
    with pytest.raises(ValueError, match="not found"):
        bt.run_apply(["ffffff:x"], TODAY)
    assert not _backups(state)


def test_retype_accepted_item(state):
    out = bt.run_apply(["bbbbbb:act"], TODAY)
    i = _get(state, "bbbbbb")
    assert i["type"] == "act" and "retyped" in out


def test_a_on_accepted_is_noted_noop(state):
    out = bt.run_apply(["bbbbbb:a"], TODAY)
    assert "already accepted" in out and "applied 0" in out


def test_original_tokens_regression(state):
    bt.run_apply(["aaaaaa1:a", "cccccc:d", "aaaaab:x", "dddddd:p", "bbbbbb:w2026-11-20"], TODAY)
    assert _get(state, "aaaaaa1")["source_analyzed"] is None
    assert _get(state, "cccccc")["status"] == "complete"
    assert _get(state, "aaaaab")["status"] == "dropped"
    assert _get(state, "dddddd")["pinned"] is True
    b = _get(state, "bbbbbb")
    assert b["status"] == "deferred" and b["deferred_to"] == "2026-11-20"


def test_acceptance_batch(state):
    bt.run_apply("cccccc:drop dddddd:defer:tomorrow aaaaab:done bbbbbb:defer:wednesday aaaaaa1:delegate:colin".split(), TODAY)
    assert _get(state, "dddddd")["deferred_to"] == "2026-11-13"
    assert _get(state, "bbbbbb")["deferred_to"] == "2026-11-18"
    a = _get(state, "aaaaaa1")
    assert a["type"] == "delegate" and a["owner"] == "colin" and a["source_analyzed"] is None
