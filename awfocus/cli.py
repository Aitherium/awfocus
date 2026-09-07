"""awfocus CLI — see, search and steer every Claude session from one command.

    awfocus                 list what is running right now
    awfocus <query>         search every transcript for the query
    awfocus open <id>       open that session in a terminal tab
    awfocus ask <id> <text>  deliver a message to a session's mailbox
    awfocus remote          sessions seen on the relay (other machines)
    awfocus --self-test     prove the contract, offline

Output is deliberately narrow lines — this command replaces squinting at
twelve terminal tabs, not with twelve tables.
"""

from __future__ import annotations

import argparse
import os
import json
import shutil
import subprocess
import sys

from . import __version__
from .focus import open_session
from .search import search
from .sessions import find_session, list_sessions
from .steer import ask as steer_ask

_COMMANDS = {"list", "search", "open", "ask", "remote"}


def _fmt_last(text: str) -> str:
    text = " ".join(text.split())
    return text[:100] + ("…" if len(text) > 100 else "")


def _print_sessions(sessions) -> None:
    for s in sessions:
        tag = "BUSY" if s.status == "busy" else "idle"
        title = s.title or s.id[:8]
        line = "%s  %s  (%s)  [%s]  %s" % (
            s.id[:8], title, s.age, tag, s.branch or "-")
        print(line)
        if s.cwd and s.cwd != os.getcwd():
            print("    " + s.cwd)
        if s.last_prompt:
            print("    last: " + _fmt_last(s.last_prompt))


def cmd_list(args) -> int:
    sessions = list_sessions(live_only=not getattr(args, "all", False))
    if not sessions:
        print("no sessions %s — start one, or run `awfocus <query>` to search history"
              % ("live" if not args.all else "found"))
        return 0
    scope = "live" if not getattr(args, "all", False) else "in the registry"
    print("%d session(s) %s:\n" % (len(sessions), scope))
    _print_sessions(sessions)
    return 0


def cmd_search(args) -> int:
    live_ids = {s.id for s in list_sessions(live_only=True)}
    hits = search(args.query, project=args.project, live_ids=live_ids)
    if not hits:
        print("nothing found for %r" % args.query)
        return 1
    for h in hits[: args.limit]:
        if not h.session_id:
            print(h.snippet)
            continue
        marker = "LIVE" if h.live else "    "
        print("%s  %d hit%s  %s  %s  %s" % (
            marker, h.matches, "s" if h.matches != 1 else "",
            h.title or h.session_id[:8], h.age, h.project))
        if h.snippet:
            print("    " + _fmt_last(h.snippet))
    if len(hits) > args.limit:
        print("… %d more; narrow with a more specific query" % (len(hits) - args.limit))
    return 0


def _resolve(needle: str):
    matches = find_session(needle)
    if not matches:
        print("no session matches %r — try `awfocus <query>` to search history" % needle)
        return None
    if len(matches) > 1:
        print("matches:")
        for m in matches[:10]:
            print("  %s  %s  (%s)  %s" % (m.id[:8], m.title or "?", m.age,
                                           "live" if m.live else "dead"))
        print("be more specific (full id or a longer title substring)")
        return None
    return matches[0]


def cmd_open(args) -> int:
    session = _resolve(args.what)
    if session is None:
        return 1
    print(open_session(session, dry_run=args.dry_run))
    return 0


def cmd_ask(args) -> int:
    session = _resolve(args.what)
    if session is None:
        return 1
    ok, result = steer_ask(session.id, " ".join(args.text))
    print(result if ok else "ERROR: " + result)
    if ok and not session.live:
        print("note: %s is not running — the message waits in its mailbox "
              "until the session is resumed and the owner next prompts it"
              % (session.title or session.id[:8]))
    return 0 if ok else 1


def cmd_remote(args) -> int:
    """Best-effort cross-machine view: the presence envelopes the
    aither-presence hook posts to #claude-sessions on the relay."""
    relay = shutil.which("awrelay")
    if not relay:
        print("awrelay not installed — the relay view needs it (pip install awrelay)")
        return 2
    try:
        proc = subprocess.run(
            [relay, "history", "#claude-sessions"],
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired) as exc:
        print("relay history failed: %s" % exc)
        return 2
    if proc.returncode != 0:
        print("relay history exited %d: %s" % (proc.returncode, proc.stderr.strip()[:200]))
        return 2
    seen = {}
    for line in proc.stdout.splitlines():
        if '"type": "presence"' not in line:
            continue
        try:
            envelope = json.loads(line[line.index("```awrelay") + 10:].strip("` \n"))
            payload = envelope.get("payload", {})
        except (ValueError, KeyError):
            continue
        sid = str(payload.get("session_id") or "").split(":")[-1]
        if not sid:
            continue
        seen[sid] = {
            "state": payload.get("state"),
            "cwd": payload.get("cwd"),
            "branch": payload.get("branch"),
            "at": payload.get("taken_at"),
        }
    if not seen:
        print("no presence envelopes on the relay channel")
        return 0
    print("%d session(s) reported on the relay:" % len(seen))
    for sid, info in sorted(seen.items(), key=lambda kv: kv[1]["at"] or "", reverse=True):
        print("%s  %-9s  %s  %s" % (sid[:8], info["state"] or "?", info["cwd"] or "-",
                                    info["branch"] or ""))
    return 0


def self_test() -> int:
    import tempfile
    from pathlib import Path

    ok = True

    def check(label, cond):
        nonlocal ok
        print("  %s %s" % ("ok  " if cond else "FAIL", label))
        ok = ok and cond

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # ── registry parse ────────────────────────────────────────────────
        (root / "sessions").mkdir()
        live_json = {
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
        (root / "sessions" / "live.json").write_bytes(
            json.dumps(live_json).encode("utf-8"))
        import awfocus.sessions as sessions_mod
        old_dir = sessions_mod.DEFAULT_SESSIONS_DIR
        old_state = sessions_mod.DEFAULT_STATE_DIR
        sessions_mod.DEFAULT_SESSIONS_DIR = root / "sessions"
        sessions_mod.DEFAULT_STATE_DIR = root / "state"
        live = sessions_mod.list_sessions(live_only=True)
        all_s = sessions_mod.list_sessions(live_only=False)
        check("registry parses and live-only filters", len(live) == 1 and len(all_s) == 2)
        check("find by id prefix", sessions_mod.find_session("abc12345")[0].id == all_s[0].id)
        check("find by title substring", sessions_mod.find_session("two")[0].id == all_s[1].id)
        sessions_mod.DEFAULT_SESSIONS_DIR = old_dir
        sessions_mod.DEFAULT_STATE_DIR = old_state

        # ── transcript search ─────────────────────────────────────────────
        proj = root / "projects" / "C--Test"
        proj.mkdir(parents=True)
        (proj / "deadbeef-0000-0000-0000-000000000001.jsonl").write_text(
            json.dumps({"type": "user", "message": {"content": "fix the flux emitter"}}) + "\n" +
            json.dumps({"type": "assistant", "message": {"content": "done"}}) + "\n",
            encoding="utf-8")
        (proj / "cafebabe-0000-0000-0000-000000000002.jsonl").write_text(
            json.dumps({"type": "user", "message": {"content": "nothing here"}}) + "\n",
            encoding="utf-8")
        import awfocus.search as search_mod
        old_proj = search_mod.DEFAULT_PROJECTS_DIR
        search_mod.DEFAULT_PROJECTS_DIR = root / "projects"
        hits = search_mod.search("flux")
        check("search finds the matching transcript", len(hits) == 1)
        check("search snippet contains the hit", hits[0].matches == 1 and "flux" in hits[0].snippet)
        check("search respects the project filter",
              search_mod.search("flux", project="Other") == [])
        search_mod.DEFAULT_PROJECTS_DIR = old_proj

        # ── steer mailbox ─────────────────────────────────────────────────
        import awfocus.steer as steer_mod
        old_steer = steer_mod.DEFAULT_STEER_ROOT
        steer_mod.DEFAULT_STEER_ROOT = root / "steer"
        ok_ask, path = steer_mod.ask("abc12345", "answer me")
        check("ask writes into the mailbox", ok_ask and Path(path).is_file())
        check("ask carries the question marker",
              Path(path).read_text(encoding="utf-8").startswith(steer_mod.MARKER))
        bad, _ = steer_mod.ask("../../etc", "x")
        check("ask refuses a traversal id", not bad)
        steer_mod.DEFAULT_STEER_ROOT = old_steer

        # ── focus shape ───────────────────────────────────────────────────
        import awfocus.focus as focus_mod
        from awfocus.sessions import Session
        live_s = Session(id="abc12345-def0-0000-0000-000000000001", title="one",
                         cwd="C:\\x", live=True)
        check("open refuses an already-live session",
              "already live" in focus_mod.open_session(live_s))
        dead_s = Session(id="abc12345-def0-0000-0000-000000000002", title="two",
                         cwd="C:\\y", live=False)
        argsv = focus_mod.open_command(dead_s)
        encoded = argsv[-1]
        import base64
        payload = base64.b64decode(encoded).decode("utf-16-le")
        check("focus payload carries the session id",
              "claude --resume abc12345-def0-0000-0000-000000000002" in payload)
        check("focus payload scrubs inherited colour vars", "NO_COLOR" in payload)

    print("\nawfocus --self-test: %s" % ("ALL PASS" if ok else "FAILURES"))
    return 0 if ok else 1


def main(argv=None) -> int:
    # GENERATED doctor intercept (gen_aw_doctor.py) -- do not edit
    _dv = locals().get("argv")
    if (_dv if _dv is not None else __import__("sys").argv[1:])[:1] == ["doctor"]:
        from ._doctor import report
        return report()
    # GENERATED repo-state intercept (gen_aw_doctor.py) -- do not edit
    try:
        from awgit import state as _aw_state
    except Exception:
        _aw_state = None
    if _aw_state is not None:
        _sv = locals().get("argv")
        if _aw_state.cli_banner(_sv if _sv is not None else __import__("sys").argv[1:]):
            return 0
    # The prompts and transcripts are UTF-8; the console may be cp1252. A
    # transcript character that cannot encode must never kill the listing.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            continue  # best-effort; a non-reconfigurable stream is not fatal
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(
        prog="awfocus",
        description="See, search and steer every Claude Code session from one command.",
    )
    parser.add_argument("--version", action="version",
                        version="awfocus %s" % __version__)
    parser.add_argument("--self-test", action="store_true",
                        help="prove the contract, offline")

    sub = parser.add_subparsers(dest="command")

    p_list = sub.add_parser("list", help="show live sessions (default)")
    p_list.add_argument("--all", action="store_true", help="include dead registry entries")
    p_list.set_defaults(fn=cmd_list)

    p_search = sub.add_parser("search", help="search every session transcript")
    p_search.add_argument("query")
    p_search.add_argument("-p", "--project", default=None,
                          help="only search one project dir (substring)")
    p_search.add_argument("--limit", type=int, default=20)
    p_search.set_defaults(fn=cmd_search)

    p_open = sub.add_parser("open", help="open a session in a terminal tab")
    p_open.add_argument("what", help="session id, id prefix, or title substring")
    p_open.add_argument("--dry-run", action="store_true")
    p_open.set_defaults(fn=cmd_open)

    p_ask = sub.add_parser("ask", help="deliver a message to a session's mailbox")
    p_ask.add_argument("what")
    p_ask.add_argument("text", nargs="+")
    p_ask.set_defaults(fn=cmd_ask)

    p_remote = sub.add_parser("remote", help="sessions seen on the relay channel")
    p_remote.set_defaults(fn=cmd_remote)

    # Bare `awfocus <query>` searches; bare `awfocus` lists. A first token
    # that is not a command (and not a flag) is a search query, never a typo
    # of a subcommand — the search half is the point of this tool.
    if argv and argv[0] not in _COMMANDS and not argv[0].startswith("-"):
        args = p_search.parse_args([" ".join(argv)])
        return cmd_search(args)

    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    if not getattr(args, "command", None):
        return cmd_list(args)

    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
