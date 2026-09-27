# Changelog

## 0.1.1 — 2026-09-26

- `awfocus mcp`: an MCP stdio server with `list_sessions`,
  `search_transcripts` (bounded: newest first, age window, time budget, and a
  `scan` block that says when the answer is partial), `focus` (reopens a
  session that is not running; never duplicates a live one) and `message`
  (peer-authority note into the target's steer mailbox). Both write verbs
  refuse your own session; `--read-only` refuses them outright.
- `search.search_recent()` and `steer.send_peer()` back the server;
  `sessions.own_session_ids()` identifies the calling session.

## 0.1.0 — 2026-08-29

- First release: list, search, open, ask, remote, self-test.
