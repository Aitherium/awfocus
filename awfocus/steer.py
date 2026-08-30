"""Steer a session — drop a message into its mailbox.

The mailbox is ``~/.aither/steer/<session-id>/*.md``, the same store the
awask drain hook (installed as the UserPromptSubmit hook) already reads: an
interactive Claude Code tab has no IPC, its hooks do, so this is the one
inbound channel that reaches the session's context. The message arrives
framed inside the session at its next prompt, and the file is archived to
``delivered/`` so it is never injected twice.

Files written here carry a marker first line (``<!-- awfocus:question -->``)
so the drain hook frames them as an owner QUESTION rather than a decision
answer — the two shapes have opposite instructions attached.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

_SESSION_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
MARKER = "<!-- awfocus:question -->"

DEFAULT_STEER_ROOT = Path.home() / ".aither" / "steer"


def steer_root() -> Path:
    env = os.getenv("AITHER_STEER_DIR", "").strip()
    return Path(env) if env else DEFAULT_STEER_ROOT


def ask(session_id: str, text: str) -> "tuple[bool, str]":
    """Queue ``text`` for ``session_id``. Returns (ok, message-or-path)."""
    if not session_id or not _SESSION_RE.match(session_id):
        return False, "refusing a malformed session id: %r" % session_id
    text = text.strip()
    if not text:
        return False, "nothing to send"
    box = steer_root() / session_id
    try:
        box.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, "cannot create mailbox: %s" % exc
    target = box / ("%d-awfocus.md" % int(time.time() * 1000))
    body = (
        MARKER + "\n"
        "\n"
        "The owner asked this of you directly. Answer it, and if it needs a "
        "decision you cannot make, say so:\n\n"
        + text + "\n"
    )
    try:
        target.write_text(body, encoding="utf-8")
    except OSError as exc:
        return False, "cannot write mailbox: %s" % exc
    return True, str(target)
