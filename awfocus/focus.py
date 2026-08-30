"""Focus a session — open it as a terminal tab, no hunting.

Same spawn shape as the AitherResume engine's ``wt new-tab`` path (verified
against it 2026-08-29): the payload MUST go through ``-EncodedCommand``,
because ``wt`` splits its own command line on semicolons even inside quoted
arguments, and the colour-suppression env vars of the CALLING session must be
scrubbed or the resumed tab comes up monochrome.

A session that is already live is never re-opened — a second tab on one
conversation is exactly the confusion this tool exists to remove.
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
from pathlib import Path

from .sessions import Session

_PAYLOAD = (
    "Remove-Item Env:NO_COLOR, Env:CLAUDECODE, Env:CLAUDE_CODE_SESSION_ID, "
    "Env:CLAUDE_CODE_CHILD_SESSION, Env:CLAUDE_CODE_ENTRYPOINT, Env:CLAUDE_PID "
    "-ErrorAction SilentlyContinue; `$PSStyle.OutputRendering='Ansi'; "
    "claude --resume {session_id}"
)


def _wt() -> "str | None":
    if not os.name == "nt":
        return None
    return shutil.which("wt")


def _pwsh() -> str:
    found = shutil.which("pwsh")
    return found or "pwsh"


def open_command(session: Session) -> "list[str]":
    """The argv to spawn the tab (exposed for dry-run and self-test)."""
    payload = _PAYLOAD.format(session_id=session.id)
    encoded = base64.b64encode(payload.encode("utf-16-le")).decode("ascii")
    return [
        _wt() or "wt",
        "new-tab",
        "-d", session.cwd or str(Path.home()),
        "--title", session.title or session.id[:8],
        _pwsh(), "-NoExit", "-EncodedCommand", encoded,
    ]


def open_session(session: Session, dry_run: bool = False) -> str:
    if session.live:
        return "already live — %s is open; no duplicate tab" % (session.title or session.id[:8])
    if dry_run:
        return " ".join(open_command(session))
    wt = _wt()
    if not wt:
        return ("no Windows Terminal on this host — run this instead:\n  "
                + _fallback_command(session))
    try:
        subprocess.Popen(
            open_command(session),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        return "failed to spawn tab: %s" % exc
    return "opened tab for %s" % (session.title or session.id[:8])


def _fallback_command(session: Session) -> str:
    return "claude --resume %s  (cwd: %s)" % (session.id, session.cwd or ".")
