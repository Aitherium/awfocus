"""An MCP server for awfocus — let an agent see and reach its sibling sessions.

    pip install "awfocus[mcp]"

then point a client at `awfocus mcp`:

    {"mcpServers": {"awfocus": {"command": "awfocus", "args": ["mcp"]}}}

WHY THIS EXISTS
---------------
A box running a dozen Claude Code sessions has no way for one of them to know
what the others are doing, find the one that already solved a problem, or
hand it a note. The data is all on disk (the presence registry, the per-pid
state files, the transcripts, the steer mailbox); this serves it as four tools.

TOOLS
-----
* ``list_sessions``      read-only. Live sessions (optionally dead ones too).
* ``search_transcripts`` read-only. Bounded: newest transcripts first, an age
                         window and a time budget, and it SAYS when the answer
                         is partial — a full scan of a busy box is many GB.
* ``focus``              opens a NOT-running session in a terminal tab
                         (``claude --resume``). A live session is never opened
                         twice; your own session is refused.
* ``message``            drops a note into another session's steer mailbox —
                         the channel awfocus already uses, drained by the
                         UserPromptSubmit hook at that session's next prompt.
                         Written with ``authority="peer"``: an agent is not the
                         owner, and the receiving session is told so. Refuses
                         your own session.

Nothing here deletes, kills or interrupts anything. ``--read-only`` (or
``AWFOCUS_MCP_READONLY=1``) makes ``focus`` and ``message`` refuse too.

SDK VERSION
-----------
The rest of the family (awgraph, awrelay) is written against the ``mcp`` 2.x
``MCPServer`` API. This module takes 2.x when it is there and falls back to the
1.x ``FastMCP`` class, which exposes the same ``tool()`` / ``list_tools()`` /
``call_tool()`` / ``run_stdio_async()`` surface for what this server uses —
so it serves on a box whose ``mcp`` is still 1.x instead of raising.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

_INSTALL_HINT = (
    "The MCP server needs the `mcp` package: pip install \"awfocus[mcp]\". "
    "Raised rather than degraded, because an MCP server that starts and "
    "serves no tools looks to the client exactly like a server with nothing "
    "to offer."
)

_READONLY_ENV = "AWFOCUS_MCP_READONLY"
_MAX_TEXT = 8000


def _server_class():
    try:
        from mcp.server import MCPServer  # 2.x
        return MCPServer
    except ImportError:
        pass  # 1.x install: fall through to FastMCP below
    try:
        from mcp.server.fastmcp import FastMCP  # 1.x
        return FastMCP
    except ImportError as exc:
        raise ImportError(_INSTALL_HINT) from exc


def _read_only() -> bool:
    return os.getenv(_READONLY_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def _session_dict(s, own: "set[str]") -> "dict[str, Any]":
    last = " ".join((s.last_prompt or "").split())
    return {
        "id": s.id,
        "short_id": s.id[:8],
        "title": s.title,
        "name": s.name,
        "cwd": s.cwd,
        "branch": s.branch,
        "status": s.status or "unknown",
        "live": s.live,
        "age": s.age,
        "last_prompt": last[:300] + ("…" if len(last) > 300 else ""),
        "transcript": s.file,
        "is_you": s.id in own,
    }


def resolve(needle: str) -> "tuple[Any, str]":
    """(session, "") on one match, (None, reason) otherwise.

    Exact id, then an id prefix (4+ chars), then a substring of the title or
    the Claude-side session name. Live sessions win over dead ones on a
    substring match; anything still ambiguous is refused with the candidates
    listed, never guessed.
    """
    from awfocus.sessions import list_sessions

    needle = (needle or "").strip()
    if not needle:
        return None, "no session given"
    every = list_sessions(live_only=False)
    exact = [s for s in every if s.id == needle]
    if exact:
        return exact[0], ""
    if len(needle) >= 4:
        pref = [s for s in every if s.id.startswith(needle)]
        if len(pref) == 1:
            return pref[0], ""
        if len(pref) > 1:
            return None, _ambiguous(needle, pref)
    low = needle.lower()
    sub = [s for s in every
           if low in (s.title or "").lower() or low in (s.name or "").lower()]
    live = [s for s in sub if s.live]
    pool = live or sub
    if len(pool) == 1:
        return pool[0], ""
    if not pool:
        return None, ("no session matches %r — call list_sessions for ids"
                      % needle)
    return None, _ambiguous(needle, pool)


def _ambiguous(needle: str, matches) -> str:
    rows = ", ".join("%s (%s%s)" % (m.id[:8], m.title or m.name or "?",
                                   "" if m.live else ", dead")
                     for m in matches[:10])
    return "%r matches %d sessions: %s — pass a full id" % (needle, len(matches), rows)


def build_server():
    """Construct the MCP server. Raises ImportError if `mcp` is absent."""
    server_cls = _server_class()
    server = server_cls(
        name="awfocus",
        instructions=(
            "The other Claude Code sessions running on this machine. "
            "list_sessions shows who is running, where, and what they were "
            "last asked; search_transcripts finds the session that discussed "
            "something; message hands another session a note it reads at its "
            "next prompt; focus reopens a session that is not running."
        ),
    )

    @server.tool(
        name="list_sessions",
        description=(
            "List the Claude Code sessions on this machine: id, title, cwd, "
            "branch, busy/idle status, age, the last prompt it was given, and "
            "`is_you` for your own session. Live sessions only unless "
            "include_dead is true. Read-only."
        ),
    )
    async def list_sessions(include_dead: bool = False) -> str:
        from awfocus.sessions import list_sessions as _list
        from awfocus.sessions import own_session_ids

        own = own_session_ids()
        rows = [_session_dict(s, own) for s in _list(live_only=not include_dead)]
        return json.dumps({"count": len(rows), "you": sorted(own),
                           "sessions": rows})

    @server.tool(
        name="search_transcripts",
        description=(
            "Search session transcripts for a phrase (case-insensitive "
            "substring) and rank the sessions that contain it, live ones "
            "first, each with a snippet and whether it is still running. "
            "Bounded for speed: only transcripts modified in the last `days` "
            "(default 2; 0 = all), newest first, stopping after "
            "`time_budget_s`. The `scan` block says whether the result is "
            "complete — if not, narrow `project` or raise the budget rather "
            "than concluding nothing exists. live_only restricts to running "
            "sessions. Read-only."
        ),
    )
    async def search_transcripts(query: str, project: str = "", days: float = 2.0,
                                 limit: int = 20, live_only: bool = False,
                                 time_budget_s: float = 20.0) -> str:
        import asyncio

        from awfocus.search import search_recent
        from awfocus.sessions import list_sessions as _list

        if not query.strip():
            return json.dumps({"error": "empty query"})
        live_ids = {s.id for s in _list(live_only=True)}
        limit = max(1, min(int(limit), 200))
        budget = max(1.0, min(float(time_budget_s), 300.0))
        hits, info = await asyncio.to_thread(
            search_recent, query, project or None, live_ids,
            float(days) if days and days > 0 else None, budget,
            live_ids if live_only else None,
        )
        rows = [{
            "session_id": h.session_id, "title": h.title, "project": h.project,
            "matches": h.matches, "live": h.live, "age": h.age,
            "last_activity": h.last_activity, "snippet": h.snippet,
            "transcript": str(h.path),
        } for h in hits[:limit]]
        return json.dumps({"query": query, "total_sessions_matched": len(hits),
                           "hits": rows, "scan": info})

    @server.tool(
        name="focus",
        description=(
            "Bring a session up: a session that is NOT running is reopened "
            "(`claude --resume`) in a new Windows Terminal tab in its own cwd. "
            "A live session is never opened twice — you get its details "
            "instead. Your own session is refused. `session` is a full id, an "
            "id prefix, or a title/name substring. dry_run returns the command "
            "without spawning anything."
        ),
    )
    async def focus(session: str, dry_run: bool = False) -> str:
        from awfocus.focus import open_session
        from awfocus.sessions import own_session_ids

        target, why = resolve(session)
        if target is None:
            return json.dumps({"ok": False, "error": why})
        own = own_session_ids()
        if target.id in own:
            return json.dumps({"ok": False,
                               "error": "refused: %s is your own session" % target.id[:8]})
        if target.live:
            return json.dumps({"ok": True, "action": "none",
                               "reason": "already live — not opening a duplicate tab",
                               "session": _session_dict(target, own)})
        if _read_only() and not dry_run:
            return json.dumps({"ok": False, "error": "refused: server is read-only (%s)"
                               % _READONLY_ENV})
        result = open_session(target, dry_run=dry_run)
        return json.dumps({"ok": not result.startswith("failed"),
                           "action": "dry_run" if dry_run else "open",
                           "result": result, "session_id": target.id})

    @server.tool(
        name="message",
        description=(
            "Send another Claude Code session a note. It lands in that "
            "session's steer mailbox and is injected into its context at its "
            "NEXT prompt (not instantly), marked as a peer message — it carries "
            "no owner authority, so ask, do not order. Refuses your own "
            "session and ambiguous targets. `session` is a full id, an id "
            "prefix, or a title/name substring; list_sessions first."
        ),
    )
    async def message(session: str, text: str) -> str:
        from awfocus.sessions import own_session_ids
        from awfocus.steer import send_peer

        if _read_only():
            return json.dumps({"ok": False, "error": "refused: server is read-only (%s)"
                               % _READONLY_ENV})
        if not text or not text.strip():
            return json.dumps({"ok": False, "error": "nothing to send"})
        if len(text) > _MAX_TEXT:
            return json.dumps({"ok": False, "error": "message over %d chars — "
                               "put the detail in a file and send its path" % _MAX_TEXT})
        target, why = resolve(session)
        if target is None:
            return json.dumps({"ok": False, "error": why})
        own = own_session_ids()
        if target.id in own:
            return json.dumps({"ok": False,
                               "error": "refused: %s is your own session" % target.id[:8]})
        sender = "awfocus-mcp:%s" % (sorted(own)[0][:8] if own else "unknown-session")
        ok, result = send_peer(target.id, text, sender)
        if not ok:
            return json.dumps({"ok": False, "error": result})
        return json.dumps({
            "ok": True, "session_id": target.id, "mailbox_file": result,
            "delivery": ("at that session's next prompt" if target.live else
                         "session is not running — waits until it is resumed "
                         "and next prompted"),
        })

    return server


def main(argv: "list[str] | None" = None) -> int:
    """Serve over stdio. Blocks until the client disconnects."""
    import asyncio

    argv = list(sys.argv[2:] if argv is None else argv)
    if "--read-only" in argv:
        os.environ[_READONLY_ENV] = "1"
    try:
        server = build_server()
    except ImportError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        asyncio.run(server.run_stdio_async())
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
