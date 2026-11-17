"""Measures wasted turns: API calls spent on errors, re-reads, go-aheads and an overfull context."""

import collections
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from repeat_o_scope import transcripts

_LOGGER = logging.getLogger(__name__)

KINDS = ('refused_edit', 'reread', 'failed', 'denied', 'bare_yes', 'long_context')
_REFUSED_EDIT = 'has not been read yet'
_EDITS = frozenset({'Edit', 'Write', 'NotebookEdit'})
# API price ratios against an input token: the best public proxy for how much of a usage limit a call takes.
_WEIGHTS = {
    'input_tokens': 1.0,
    'cache_creation_input_tokens': 1.25,
    'cache_read_input_tokens': 0.1,
    'output_tokens': 5.0,
}


@dataclass(frozen=True)
class Tally:
    turns: int
    tokens: int
    weight: float


@dataclass(frozen=True)
class Measurement:
    days: int
    total: Tally
    waste: dict[str, Tally]


def measure(*, days: int, root: Path, since: datetime) -> Measurement:
    """Returns the turns and tokens spent in the window, and those wasted per kind."""
    calls: dict[str, dict] = {}
    charges: dict[str, str] = {}
    issuer: dict[str, str] = {}
    seen: dict[tuple, set[tuple]] = collections.defaultdict(set)
    asked: set[tuple] = set()
    go_ahead: set[tuple] = set()
    for e in transcripts.iter_entries(root=root, since=since, subagents=True):
        context = (e.get('sessionId'), e.get('agentId'))
        if e.get('isCompactSummary'):
            # Compaction drops what was read, so reading it again is needed.
            seen[context].clear()
        # Condition copied from `iter_turn_ends`: a reply counts only after Claude said something in that session.
        if (text := transcripts.typed_text(e)) and context in asked and transcripts.is_go_ahead(text):
            go_ahead.add(context)
        message = e.get('message') or {}
        call_id, usage = message.get('id'), message.get('usage')
        is_call = e.get('type') == 'assistant' and isinstance(call_id, str) and isinstance(usage, dict)
        if is_call:
            calls.setdefault(call_id, usage)
            asked.add(context)
            if context in go_ahead:
                go_ahead.discard(context)
                charges.setdefault(call_id, 'bare_yes')
        for block in _blocks(message):
            if block.get('type') == 'tool_use' and is_call:
                issuer[block.get('id')] = call_id
                if _is_reread(block, seen=seen[context]):
                    charges.setdefault(call_id, 'reread')
            elif block.get('type') == 'tool_result' and block.get('is_error'):
                if (by := issuer.get(block.get('tool_use_id'))) is not None:
                    charges.setdefault(by, _error_kind(transcripts.error_line(block.get('content'))))
    waste = {kind: _tally([calls[c] for c, k in charges.items() if k == kind]) for kind in KINDS}
    total = _tally(list(calls.values()))
    _LOGGER.info('Measured `%s` turns, `%s` wasted.', total.turns, len(charges))
    return Measurement(days=days, total=total, waste=waste)


def _blocks(message: dict) -> Iterator[dict]:
    content = message.get('content')
    if isinstance(content, list):
        yield from (b for b in content if isinstance(b, dict))


def _is_reread(block: dict, *, seen: set[tuple]) -> bool:
    """Returns whether a tool call reads a range already read in this context; edits make a file new again."""
    tool_input = block.get('input') if isinstance(block.get('input'), dict) else {}
    path = tool_input.get('file_path') or tool_input.get('notebook_path')
    if not isinstance(path, str):
        return False
    if block.get('name') in _EDITS:
        seen.difference_update({r for r in seen if r[0] == path})
        return False
    if block.get('name') != 'Read':
        return False
    read = (path, tool_input.get('offset'), tool_input.get('limit'))
    if read in seen:
        return True
    seen.add(read)
    return False


def _error_kind(error: str) -> str:
    if transcripts.is_denial(error):
        return 'denied'
    return 'refused_edit' if _REFUSED_EDIT in error else 'failed'


def _tally(usages: list[dict]) -> Tally:
    return Tally(turns=len(usages), tokens=sum(map(_tokens, usages)), weight=sum(map(_weight, usages)))


def _tokens(usage: dict) -> int:
    return sum(_count(usage, key) for key in _WEIGHTS)


def _weight(usage: dict) -> float:
    """Returns how much of a usage limit a call takes, in input-token equivalents."""
    return sum(_count(usage, key) * w for key, w in _WEIGHTS.items())


def _count(usage: dict, key: str) -> int:
    value = usage.get(key)
    return value if isinstance(value, int) else 0
