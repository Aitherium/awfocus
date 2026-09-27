"""Transcript search — find the session that said it, without opening tabs.

The transcripts are one JSONL file per session at
``~/.claude/projects/<munged-cwd>/<session-id>.jsonl`` — the same files the
resume engine reads to derive titles. Searching is a line-scan over those
files; matching is done on the raw line first (cheap) and the JSON is decoded
only for lines that hit, so a big transcript costs a decode per hit, not per
line.

Override for the self-test: AWFOCUS_PROJECTS_DIR.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

DEFAULT_PROJECTS_DIR = Path.home() / ".claude" / "projects"
_MAX_FILE_BYTES = 200 * 1024 * 1024      # skip monsters, say so
_MAX_MATCHES_PER_FILE = 500              # a pathological file must not drown the rest
_SNIPPET_RADIUS = 110


def projects_dir() -> Path:
    env = os.getenv("AWFOCUS_PROJECTS_DIR", "").strip()
    return Path(env) if env else DEFAULT_PROJECTS_DIR


@dataclass
class Hit:
    session_id: str
    title: str
    path: Path
    project: str                # munged dir name, e.g. C--AitherOS-Fresh
    matches: int
    last_activity: str = ""     # ISO from the newest line that matched
    snippet: str = ""
    live: bool = False

    @property
    def age(self) -> str:
        if not self.last_activity:
            return "?"
        try:
            ts = datetime.fromisoformat(self.last_activity.replace("Z", "+00:00"))
        except ValueError:
            return "?"
        if ts.tzinfo is None:
            from datetime import timezone
            ts = ts.replace(tzinfo=timezone.utc)
        seconds = max(0, int((datetime.now(ts.tzinfo) - ts).total_seconds()))
        if seconds < 3600:
            return "%dm" % (seconds // 60)
        if seconds < 86400:
            return "%dh" % (seconds // 3600)
        return "%dd" % (seconds // 86400)


def _title_for(path: Path) -> str:
    """First user text message, truncated — the same heuristic the resume
    engine uses, so titles agree between awfocus and resume-all."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for _ in range(80):
                line = fh.readline()
                if not line:
                    break
                if '"type":"user"' not in line and '"type": "user"' not in line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                content = event.get("message", {}).get("content")
                text = ""
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    parts = []
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            parts.append(str(block.get("text", "")))
                    text = " ".join(parts)
                text = " ".join(text.split())
                if text:
                    return text[:70]
    except OSError:
        return path.stem[:8]
    return path.stem[:8]


def _line_text(line: str) -> str:
    """Best-effort readable text for a JSONL line (snippets only — a failure
    falls back to the raw line, never to nothing)."""
    try:
        event = json.loads(line)
    except ValueError:
        return line.strip()
    content = event.get("message", {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text" and block.get("text"):
                    parts.append(str(block["text"]))
                elif block.get("type") == "tool_use" and block.get("name"):
                    parts.append("[tool:%s]" % block["name"])
        return " ".join(parts)
    return line.strip()


def _line_ts(line: str) -> str:
    """Best-effort event timestamp from a JSONL line (snippet decoration)."""
    try:
        event = json.loads(line)
        return str(event.get("timestamp") or "")
    except ValueError:
        return ""


def _snippet(text: str, needle: str) -> str:
    idx = text.lower().find(needle)
    if idx < 0:
        idx = 0
    start = max(0, idx - _SNIPPET_RADIUS)
    end = min(len(text), idx + len(needle) + _SNIPPET_RADIUS)
    snip = " ".join(text[start:end].split())
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return prefix + snip + suffix


def _scan_file(path: Path, needle: str) -> "tuple[int, str, str]":
    """(match count, first snippet, newest matching timestamp) for one
    transcript. An unreadable file is (0, "", "") — skipped, never fatal."""
    count = 0
    first_snippet = ""
    last_ts = ""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if needle not in line.lower():
                    continue
                text = _line_text(line)
                if needle not in text.lower():
                    continue
                count += 1
                if not first_snippet:
                    first_snippet = _snippet(text, needle)
                # The line's own timestamp is the best activity signal.
                ts = _line_ts(line)
                if ts:
                    last_ts = ts
                if count >= _MAX_MATCHES_PER_FILE:
                    break
    except OSError:
        return 0, "", ""
    return count, first_snippet, last_ts


def _owner_of(path: Path, root: Path) -> "tuple[str, str]":
    """(project dir, owning session id) for a transcript under ``root``.

    ``<proj>/<sid>.jsonl`` is the session itself; anything deeper
    (``<proj>/<sid>/subagents/agent-x.jsonl``) belongs to ``<sid>``.
    """
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return path.parent.name, path.stem
    if len(parts) <= 2:
        return (parts[0] if len(parts) == 2 else ""), path.stem
    return parts[0], parts[1]


def search_recent(query: str, project: "str | None" = None,
                  live_ids: "set[str] | None" = None,
                  max_age_days: "float | None" = 2.0,
                  time_budget_s: "float | None" = 20.0,
                  only_ids: "set[str] | None" = None,
                  ) -> "tuple[list[Hit], dict]":
    """Bounded search for callers that cannot wait minutes (the MCP server).

    A full scan of a busy box's transcripts is many GB and takes minutes; this
    walks transcripts NEWEST FIRST, skips ones not modified within
    ``max_age_days``, and stops at ``time_budget_s``. The returned ``info``
    says exactly what was and was not scanned, so a partial answer is never
    presented as a complete one.
    """
    import time as _time

    needle = query.lower()
    info = {"scanned": 0, "skipped_old": 0, "skipped_big": 0,
            "not_scanned_budget": 0, "complete": True,
            "max_age_days": max_age_days, "time_budget_s": time_budget_s}
    if not needle:
        return [], info
    live_ids = live_ids or set()
    root = projects_dir()
    if not root.is_dir():
        info["complete"] = False
        info["error"] = "no transcripts dir at %s" % root
        return [], info
    cutoff = (_time.time() - max_age_days * 86400.0) if max_age_days else None
    candidates = []
    try:
        for path in root.glob("*/**/*.jsonl"):
            project_name, owner = _owner_of(path, root)
            if project and project not in project_name:
                continue
            if only_ids is not None and owner not in only_ids:
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            if cutoff is not None and st.st_mtime < cutoff:
                info["skipped_old"] += 1
                continue
            if st.st_size > _MAX_FILE_BYTES:
                info["skipped_big"] += 1
                continue
            candidates.append((st.st_mtime, path, project_name, owner))
    except OSError as exc:
        info["complete"] = False
        info["error"] = "cannot list transcripts: %s" % exc
        return [], info
    candidates.sort(key=lambda c: c[0], reverse=True)
    deadline = (_time.monotonic() + time_budget_s) if time_budget_s else None
    by_owner: "dict[str, Hit]" = {}
    for idx, (_mtime, path, project_name, owner) in enumerate(candidates):
        if deadline is not None and _time.monotonic() > deadline:
            info["not_scanned_budget"] = len(candidates) - idx
            info["complete"] = False
            break
        info["scanned"] += 1
        count, snippet, last_ts = _scan_file(path, needle)
        if count == 0:
            continue
        # A subagent transcript is folded into the session that spawned it:
        # an `agent-…` id can be neither focused nor messaged.
        main = path if path.stem == owner else root / project_name / (owner + ".jsonl")
        hit = by_owner.get(owner)
        if hit is None:
            hit = by_owner[owner] = Hit(
                session_id=owner,
                title=_title_for(main) if main.is_file() else "",
                path=main if main.is_file() else path, project=project_name,
                matches=0, live=owner in live_ids,
            )
        hit.matches += count
        if last_ts > hit.last_activity:
            hit.last_activity = last_ts
        if snippet and (not hit.snippet or path.stem == owner):
            hit.snippet = snippet
    hits = list(by_owner.values())
    if info["skipped_big"]:
        info["complete"] = False
    hits.sort(key=lambda h: (h.live, h.matches, h.last_activity), reverse=True)
    return hits, info


def search(query: str, project: "str | None" = None,
           live_ids: "set[str] | None" = None) -> "list[Hit]":
    needle = query.lower()
    if not needle:
        return []
    live_ids = live_ids or set()
    root = projects_dir()
    hits: "list[Hit]" = []
    skipped_big = 0

    if not root.is_dir():
        return hits

    try:
        files = sorted(root.glob("*/**/*.jsonl"))
    except OSError:
        return hits

    for path in files:
        project_name = path.parent.name if path.parent != root else ""
        if project and project not in project_name:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > _MAX_FILE_BYTES:
            skipped_big += 1
            continue

        count, first_snippet, last_ts = _scan_file(path, needle)
        if count == 0:
            continue
        hits.append(Hit(
            session_id=path.stem,
            title=_title_for(path),
            path=path,
            project=project_name,
            matches=count,
            last_activity=last_ts,
            snippet=first_snippet,
            live=path.stem in live_ids,
        ))

    hits.sort(key=lambda h: (h.matches, h.last_activity), reverse=True)
    if skipped_big:
        hits.append(Hit(
            session_id="",
            title="",
            path=Path(""),
            project="",
            matches=0,
            snippet="",
        ))
        hits[-1].snippet = (
            "(%d transcript(s) over %d MB skipped — search them "
            "with a tighter query)"
            % (skipped_big, _MAX_FILE_BYTES // (1024 * 1024)))
    return hits
