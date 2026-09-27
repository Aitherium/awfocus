"""The `awfocus mcp` surface must answer through the SDK's own entry points.

These drive the server's `list_tools()` / `call_tool()` — the path a client
takes — against a fabricated registry, pid-state dir, transcript tree and
steer root, so every tool is exercised end to end without touching the real
home directory. Each of the four tools has at least one test that would fail
if the tool regressed (wrong data, self not refused, duplicate tab opened,
owner authority forged).
"""

from __future__ import annotations

import asyncio
import json
import os
import time

import pytest

pytest.importorskip("mcp", reason="awfocus[mcp] not installed")

import awfocus.focus as focus_mod  # noqa: E402
import awfocus.mcp_server as mcp_server  # noqa: E402

LIVE_ID = "11111111-aaaa-0000-0000-000000000001"
SELF_ID = "22222222-bbbb-0000-0000-000000000002"
DEAD_ID = "33333333-cccc-0000-0000-000000000003"


@pytest.fixture()
def box(tmp_path, monkeypatch):
    sessions = tmp_path / "sessions"
    state = tmp_path / "state"
    projects = tmp_path / "projects" / "C--Test"
    steer = tmp_path / "steer"
    for d in (sessions, state, projects, steer):
        d.mkdir(parents=True)
    now = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    (sessions / "live.json").write_text(json.dumps({"sessions": [
        {"id": LIVE_ID, "title": "tunnel work", "cwd": "C:\\a", "branch": "dev",
         "lastPrompt": "fix the tunnel", "when": now, "live": True},
        {"id": SELF_ID, "title": "me myself", "cwd": "C:\\b", "branch": "dev",
         "lastPrompt": "hi", "when": now, "live": True},
        {"id": DEAD_ID, "title": "old flux job", "cwd": "C:\\c", "branch": "main",
         "lastPrompt": "", "when": "2026-01-01T00:00:00+00:00", "live": False},
    ]}), encoding="utf-8")
    (state / "4242.json").write_text(json.dumps(
        {"pid": 4242, "sessionId": LIVE_ID, "status": "busy", "name": "tunnel tab"}),
        encoding="utf-8")
    (state / "4343.json").write_text(json.dumps(
        {"pid": 4343, "sessionId": SELF_ID, "status": "busy"}), encoding="utf-8")
    (projects / (LIVE_ID + ".jsonl")).write_text(
        json.dumps({"type": "user", "timestamp": now,
                    "message": {"content": "the cloudflared tunnel needs a restart"}})
        + "\n", encoding="utf-8")
    old = projects / (DEAD_ID + ".jsonl")
    old.write_text(json.dumps({"type": "user",
                               "message": {"content": "tunnel from long ago"}}) + "\n",
                   encoding="utf-8")
    ancient = time.time() - 30 * 86400
    os.utime(old, (ancient, ancient))

    monkeypatch.setenv("AWFOCUS_SESSIONS_DIR", str(sessions))
    monkeypatch.setenv("AWFOCUS_STATE_DIR", str(state))
    monkeypatch.setenv("AWFOCUS_PROJECTS_DIR", str(tmp_path / "projects"))
    monkeypatch.setenv("AITHER_STEER_DIR", str(steer))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SELF_ID)
    monkeypatch.delenv("CLAUDE_PID", raising=False)
    monkeypatch.delenv("AWFOCUS_MCP_READONLY", raising=False)
    return tmp_path


def _text(result) -> str:
    if isinstance(result, tuple):  # 1.x FastMCP may return (content, structured)
        result = result[0]
    content = getattr(result, "content", result)
    return "\n".join(getattr(c, "text", "") or "" for c in content)


def _call(name: str, **args):
    server = mcp_server.build_server()
    return json.loads(_text(asyncio.run(server.call_tool(name, args))))


def test_server_declares_the_four_tools():
    server = mcp_server.build_server()
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert names == {"list_sessions", "search_transcripts", "focus", "message"}


def test_list_sessions_live_only_and_marks_self(box):
    out = _call("list_sessions")
    ids = {s["id"]: s for s in out["sessions"]}
    assert set(ids) == {LIVE_ID, SELF_ID}
    assert ids[SELF_ID]["is_you"] and not ids[LIVE_ID]["is_you"]
    assert ids[LIVE_ID]["status"] == "busy" and ids[LIVE_ID]["name"] == "tunnel tab"
    assert DEAD_ID in {s["id"] for s in _call("list_sessions", include_dead=True)["sessions"]}


def test_search_is_age_bounded_and_says_so(box):
    out = _call("search_transcripts", query="TUNNEL")
    assert [h["session_id"] for h in out["hits"]] == [LIVE_ID]
    assert out["hits"][0]["live"] is True
    assert "cloudflared" in out["hits"][0]["snippet"]
    assert out["scan"]["skipped_old"] == 1 and out["scan"]["complete"] is True
    every = _call("search_transcripts", query="tunnel", days=0)
    assert {h["session_id"] for h in every["hits"]} == {LIVE_ID, DEAD_ID}


def test_search_live_only(box):
    out = _call("search_transcripts", query="tunnel", days=0, live_only=True)
    assert [h["session_id"] for h in out["hits"]] == [LIVE_ID]


def test_message_writes_peer_authority_file(box):
    out = _call("message", session="tunnel", text="did the restart land?")
    assert out["ok"] and out["session_id"] == LIVE_ID
    body = open(out["mailbox_file"], encoding="utf-8").read()
    first = body.splitlines()[0]
    assert first.startswith('<!-- aither-steer v1 authority="peer"')
    assert 'from="awfocus-mcp:22222222"' in first
    assert "did the restart land?" in body
    assert "owner" not in first


def test_message_refuses_self(box):
    out = _call("message", session=SELF_ID, text="hello me")
    assert not out["ok"] and "your own session" in out["error"]
    assert not (box / "steer" / SELF_ID).exists()


def test_message_refuses_ambiguous(box):
    out = _call("message", session="e", text="x")  # 'tunnel work' + 'me myself'
    assert not out["ok"] and "matches 2 sessions" in out["error"]
    assert not any((box / "steer").iterdir())


def test_message_read_only(box, monkeypatch):
    monkeypatch.setenv("AWFOCUS_MCP_READONLY", "1")
    out = _call("message", session=LIVE_ID, text="x")
    assert not out["ok"] and "read-only" in out["error"]


def test_focus_never_duplicates_a_live_session(box, monkeypatch):
    spawned = []
    monkeypatch.setattr(focus_mod.subprocess, "Popen",
                        lambda *a, **k: spawned.append(a))
    out = _call("focus", session=LIVE_ID)
    assert out["ok"] and out["action"] == "none"
    assert spawned == []


def test_focus_refuses_self(box):
    out = _call("focus", session=SELF_ID)
    assert not out["ok"] and "your own session" in out["error"]


def test_focus_dry_run_resumes_dead_session(box):
    out = _call("focus", session="old flux", dry_run=True)
    assert out["ok"] and out["action"] == "dry_run" and out["session_id"] == DEAD_ID


def test_search_folds_subagent_transcripts_into_their_session(box):
    sub = box / "projects" / "C--Test" / LIVE_ID / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-abc123.jsonl").write_text(
        json.dumps({"type": "user", "message": {"content": "zeppelin subagent note"}})
        + "\n", encoding="utf-8")
    out = _call("search_transcripts", query="zeppelin")
    assert [h["session_id"] for h in out["hits"]] == [LIVE_ID]
    assert out["hits"][0]["live"] is True
    assert out["hits"][0]["transcript"].endswith(LIVE_ID + ".jsonl")
    live = _call("search_transcripts", query="zeppelin", live_only=True)
    assert [h["session_id"] for h in live["hits"]] == [LIVE_ID]
