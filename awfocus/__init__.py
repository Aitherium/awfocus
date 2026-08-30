"""awfocus — see, search and steer every Claude Code session from one command.

Reads the files the presence plane already writes (no service to run):
the live-session registry at ~/.aither/claude_sessions/live.json, the
per-pid state files at ~/.claude/sessions/*.json, the transcripts at
~/.claude/projects/*/*.jsonl, and the steer mailbox at
~/.aither/steer/<session-id>/ that the awask drain hook already consumes.
"""

__version__ = "0.1.0"
