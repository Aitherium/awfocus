"""awfocus behaviour — the CLI self-test's four halves as pytest.

`awfocus --self-test` proves the contract offline; this file is the SAME
four halves (registry parse, transcript search, steer mailbox, focus shape)
driven by pytest, so the pypi-publish workflow's `pytest tests/` gate
exercises them too. The publish gate refuses an untested package, which is
why this file exists — not because the self-test was insufficient.

Standalone by contract: nothing here imports `lib.` or `services.` (the
moat guard scans the built artifact for exactly that).
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

import awfocus.focus as focus_mod
import awfocus.search as search_mod
import awfocus.sessions as sessions_mod
import awfocus.steer as steer_mod
from awfocus.sessions import Session

LIVE_JSON = {
    "taken_at": "x",
    "sessions": [
        {"id": "abc12345-def0-0000-0000-000000000001", "title": "one",
         "cwd": "C:\\x", "branch": "dev", "lastPrompt": "hello",
         "when": "2026-08-29T00:00:00+00:00", "live": True},
        {"id": "abc12345-def0-0000-0000-000000000002", "title": "two",
         "cwd": "C:\\y", "branch": "main", "lastPrompt": "",
         "when": "2026-08-28T00:00:00+00:00", "live": False},
    ],
}

PROJECT_LINE_USER = json.dumps(
    {"type": "user", "message": {"content": "fix the flux emitter"}})
PROJECT_LINE_ASSISTANT = json.dumps(
    {"type": "assistant", "message": {"content": "done"}})
OTHER_LINE = json.dumps(
    {"type": "user", "message": {"content": "nothing here"}})


@pytest.fixture()
def awfocus_dirs(tmp_path, monkeypatch):
    """Point every awfocus module at a throwaway tree for the test."""
    (tmp_path / "sessions").mkdir()
    (tmp_path / "sessions" / "live.json").write_bytes(
        json.dumps(LIVE_JSON).encode("utf-8"))
    proj = tmp_path / "projects" / "C--Test"
    proj.mkdir(parents=True)
    (proj / "deadbeef-0000-0000-0000-000000000001.jsonl").write_text(
        PROJECT_LINE_USER + "\n" + PROJECT_LINE_ASSISTANT + "\n", encoding="utf-8")
    (proj / "cafebabe-0000-0000-0000-000000000002.jsonl").write_text(
        OTHER_LINE + "\n", encoding="utf-8")
    monkeypatch.setattr(sessions_mod, "DEFAULT_SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(sessions_mod, "DEFAULT_STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(search_mod, "DEFAULT_PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(steer_mod, "DEFAULT_STEER_ROOT", tmp_path / "steer")
    return tmp_path


def test_registry_parses_and_live_only_filters(awfocus_dirs):
    live = sessions_mod.list_sessions(live_only=True)
    all_s = sessions_mod.list_sessions(live_only=False)
    assert len(live) == 1
    assert len(all_s) == 2


def test_find_by_id_prefix_and_title_substring(awfocus_dirs):
    all_s = sessions_mod.list_sessions(live_only=False)
    assert sessions_mod.find_session("abc12345")[0].id == all_s[0].id
    assert sessions_mod.find_session("two")[0].id == all_s[1].id


def test_search_finds_matching_transcript(awfocus_dirs):
    hits = search_mod.search("flux")
    assert len(hits) == 1
    assert hits[0].matches == 1
    assert "flux" in hits[0].snippet


def test_search_respects_project_filter(awfocus_dirs):
    assert search_mod.search("flux", project="Other") == []


def test_ask_writes_into_the_mailbox(awfocus_dirs):
    ok_ask, path = steer_mod.ask("abc12345", "answer me")
    assert ok_ask
    assert path and Path(path).is_file()
    assert Path(path).read_text(encoding="utf-8").startswith(steer_mod.MARKER)


def test_ask_refuses_a_traversal_id(awfocus_dirs):
    bad, _ = steer_mod.ask("../../etc", "x")
    assert not bad


def test_open_refuses_an_already_live_session(awfocus_dirs):
    live_s = Session(id="abc12345-def0-0000-0000-000000000001", title="one",
                     cwd="C:\\x", live=True)
    assert "already live" in focus_mod.open_session(live_s)


def test_focus_payload_carries_session_id_and_scrubs_colour_vars(awfocus_dirs):
    dead_s = Session(id="abc12345-def0-0000-0000-000000000002", title="two",
                     cwd="C:\\y", live=False)
    encoded = focus_mod.open_command(dead_s)[-1]
    payload = base64.b64decode(encoded).decode("utf-16-le")
    assert "claude --resume abc12345-def0-0000-0000-000000000002" in payload
    assert "NO_COLOR" in payload
