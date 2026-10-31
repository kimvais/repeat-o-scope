"""Reads Claude Code transcripts: what the user typed, how Claude ended turns, which tool calls failed."""

import json
import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from repeat_o_scope.redaction import redact

_LOGGER = logging.getLogger(__name__)

# Harness plumbing that arrives as a user message but was not typed by the user.
_PLUMBING = re.compile(
    r'^\s*(<(command-|local-command|system-reminder|task-notification|cross-session-message|pasted_content'
    r'|bash-stdout|bash-stderr)|This session is being continued|\[Request interrupted|Base directory for this skill'
    r'|Caveat:)',
    re.IGNORECASE,
)
# A `!` command the user ran by hand.
_BASH_INPUT = re.compile(r'^\s*<bash-input>(.*?)</bash-input>', re.IGNORECASE | re.DOTALL)
# Lines that wrap a tool error without saying anything about it.
_ERROR_WRAPPER = re.compile(r'^(</?tool_use_error>|</?error>|Exit code -?\d+)$')
_ATTEMPT_KEYS = ('command', 'file_path', 'pattern', 'query', 'skill', 'url', 'prompt')


@dataclass(frozen=True)
class TurnEnd:
    closing: str
    reply: str


@dataclass(frozen=True)
class ToolFailure:
    tool: str
    attempt: str
    error: str


def iter_prompts(*, root: Path, since: datetime) -> Iterator[str]:
    """Yields the prompts the user typed, redacted."""
    for text in _iter_typed(root=root, since=since):
        if not _BASH_INPUT.match(text):
            yield redact(text)


def iter_commands(*, root: Path, since: datetime) -> Iterator[str]:
    """Yields the `!` shell commands the user ran by hand, redacted."""
    for text in _iter_typed(root=root, since=since):
        if match := _BASH_INPUT.match(text):
            yield redact(match.group(1).strip())


def iter_turn_ends(*, root: Path, since: datetime) -> Iterator[TurnEnd]:
    """Yields Claude's closing line of each turn together with the user's reply."""
    closing = {}
    for e in _iter_entries(root=root, since=since, subagents=False):
        session = e.get('sessionId')
        if e.get('type') == 'assistant' and not e.get('isSidechain'):
            if line := _last_line(_text(e)):
                closing[session] = line
        elif (text := _typed_text(e)) and not _BASH_INPUT.match(text) and session in closing:
            yield TurnEnd(closing=redact(closing.pop(session)), reply=redact(text))


def iter_tool_failures(*, root: Path, since: datetime) -> Iterator[ToolFailure]:
    """Yields every failed tool call, subagents included, with what was attempted."""
    attempts = {}
    for e in _iter_entries(root=root, since=since, subagents=True):
        content = (e.get('message') or {}).get('content')
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get('type') == 'tool_use':
                attempts[block.get('id')] = (block.get('name') or '?', _attempt(block.get('input')))
            elif block.get('type') == 'tool_result' and block.get('is_error'):
                tool, attempt = attempts.get(block.get('tool_use_id'), ('?', ''))
                yield ToolFailure(tool=tool, attempt=redact(attempt), error=redact(_error_line(block.get('content'))))


def _iter_entries(*, root: Path, since: datetime, subagents: bool) -> Iterator[dict]:
    """Yields transcript entries newer than `since`, skipping lines that are not JSON objects."""
    files = 0
    for path in sorted(root.rglob('*.jsonl')):
        if path.stat().st_mtime < since.timestamp() or (not subagents and 'subagents' in path.parts):
            continue
        files += 1
        with path.open(encoding='utf-8', errors='replace') as f:
            for line in f:
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(e, dict) and _newer(e.get('timestamp'), since):
                    yield e
    _LOGGER.info('Read `%s` transcripts from `%s`.', files, root)


def _iter_typed(*, root: Path, since: datetime) -> Iterator[str]:
    for e in _iter_entries(root=root, since=since, subagents=False):
        if text := _typed_text(e):
            yield text


def _typed_text(e: dict) -> str | None:
    """Returns the text of a message the user typed, or None for anything else."""
    if (
        e.get('type') != 'user'
        or e.get('isMeta')
        or e.get('isSidechain')
        # Scripted runs (`claude -p`, the Agent SDK) are not the user typing.
        or e.get('entrypoint') == 'sdk-cli'
    ):
        return None
    text = _text(e).strip()
    return text if text and not _PLUMBING.match(text) else None


def _text(e: dict) -> str:
    content = (e.get('message') or {}).get('content')
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ''
    return ' '.join(b.get('text', '') for b in content if isinstance(b, dict) and b.get('type') == 'text')


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ''


def _attempt(tool_input) -> str:
    if not isinstance(tool_input, dict):
        return ''
    for key in _ATTEMPT_KEYS:
        if key in tool_input:
            return str(tool_input[key])
    return json.dumps(tool_input)[:200]


def _error_line(content) -> str:
    """Returns the first line of a tool error that says something."""
    text = content if isinstance(content, str) else ' '.join(
        b.get('text', '') for b in content or [] if isinstance(b, dict) and b.get('type') == 'text'
    )
    for line in text.splitlines():
        line = line.strip()
        if line and not _ERROR_WRAPPER.match(line):
            return re.sub(r'</?tool_use_error>', '', line).strip()
    return ''


def _newer(timestamp, since: datetime) -> bool:
    if not isinstance(timestamp, str):
        return False
    try:
        return datetime.fromisoformat(timestamp) >= since
    except ValueError:
        return False
