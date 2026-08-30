"""Live session registry — who is running, where, doing what.

The data already exists; awfocus only reads it:

* ``~/.aither/claude_sessions/live.json`` — maintained by the aither-presence
  SessionStart/Stop hook (one entry per session: id, title, cwd, branch,
  lastPrompt, when, live, transcript path). The ``live`` flag is validated
  against pids by the hook's snapshot pass, so it is trusted here — never
  inferred from age.
* ``~/.claude/sessions/<pid>.json`` — one state file per running process
  (sessionId, status busy/idle, name). Adds the "what is it doing right now"
  half that the registry does not carry.

Overrides (same shape as the rest of the family's env knobs, used by the
self-test): AWFOCUS_SESSIONS_DIR and AWFOCUS_STATE_DIR.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_SESSIONS_DIR = Path.home() / ".aither" / "claude_sessions"
DEFAULT_STATE_DIR = Path.home() / ".claude" / "sessions"


def sessions_dir() -> Path:
    env = os.getenv("AWFOCUS_SESSIONS_DIR", "").strip()
    return Path(env) if env else DEFAULT_SESSIONS_DIR


def state_dir() -> Path:
    env = os.getenv("AWFOCUS_STATE_DIR", "").strip()
    return Path(env) if env else DEFAULT_STATE_DIR


@dataclass
class Session:
    id: str
    title: str
    cwd: str = ""
    branch: str = ""
    last_prompt: str = ""
    when: str = ""          # ISO timestamp from the registry
    live: bool = False
    status: str = ""        # busy / idle / "" (from the pid state file)
    name: str = ""          # claude-side session name, if any
    file: str = ""          # transcript path, if known

    @property
    def age(self) -> str:
        """'2m', '3h', '1d' — from the registry's `when`, never from anything
        else. The resume engine proved age is a liar in both directions."""
        if not self.when:
            return "?"
        try:
            ts = datetime.fromisoformat(self.when.replace("Z", "+00:00"))
        except ValueError:
            return "?"
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - ts
        seconds = max(0, int(delta.total_seconds()))
        if seconds < 60:
            return "%ds" % seconds
        if seconds < 3600:
            return "%dm" % (seconds // 60)
        if seconds < 86400:
            return "%dh" % (seconds // 3600)
        return "%dd" % (seconds // 86400)


def _read_json(path: Path) -> "dict | None":
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None


def _registry() -> "dict | None":
    return _read_json(sessions_dir() / "live.json")


def _pid_states() -> "dict[str, dict]":
    """sessionId -> {status, name, pid} from the per-pid state files."""
    out: "dict[str, dict]" = {}
    state = state_dir()
    if not state.is_dir():
        return out
    try:
        files = sorted(state.glob("*.json"))
    except OSError:
        return out
    for path in files:
        data = _read_json(path)
        if not data or not data.get("sessionId"):
            continue
        out[str(data["sessionId"])] = {
            "status": data.get("status", ""),
            "name": data.get("name", "") or "",
            "pid": data.get("pid"),
        }
    return out


def list_sessions(live_only: bool = True) -> "list[Session]":
    """Merged, recency-sorted view of the registry + pid states."""
    reg = _registry()
    states = _pid_states()
    raw = (reg or {}).get("sessions", []) if reg else []
    sessions = []
    for entry in raw:
        sid = str(entry.get("id", ""))
        if not sid:
            continue
        is_live = bool(entry.get("live"))
        state = states.get(sid, {})
        sessions.append(Session(
            id=sid,
            title=str(entry.get("title") or sid[:8]),
            cwd=str(entry.get("cwd") or ""),
            branch=str(entry.get("branch") or ""),
            last_prompt=str(entry.get("lastPrompt") or ""),
            when=str(entry.get("when") or ""),
            live=is_live,
            status=str(state.get("status") or ""),
            name=str(state.get("name") or ""),
            file=str(entry.get("file") or ""),
        ))
    sessions.sort(key=lambda s: s.when, reverse=True)
    if live_only:
        sessions = [s for s in sessions if s.live]
    return sessions


def find_session(needle: str, include_dead: bool = True) -> "list[Session]":
    """Resolve an id, an 8-char prefix, or a title substring."""
    needle_l = needle.lower()
    matches = []
    for s in list_sessions(live_only=False):
        if needle == s.id or needle == s.id[:8]:
            matches.append(s)
        elif needle_l and (needle_l in s.title.lower()
                           or needle_l in s.cwd.lower()):
            matches.append(s)
    if include_dead:
        return matches
    return [s for s in matches if s.live]
