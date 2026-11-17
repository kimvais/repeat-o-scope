"""Measures wasted turns: API calls spent on errors, re-reads, go-aheads, expired caches and an overfull context."""

import collections
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import pairwise
from pathlib import Path

from repeat_o_scope import transcripts
from repeat_o_scope.redaction import redact

_LOGGER = logging.getLogger(__name__)

KINDS = ('refused_edit', 'reread', 'failed', 'denied', 'bare_yes', 'cache_rebuild', 'long_context')
NO_EVIDENCE = '(no text)'
_REFUSED_EDIT = 'has not been read yet'
_EDITS = frozenset({'Edit', 'Write', 'NotebookEdit'})
_CONTEXT_WINDOWS = (200_000, 1_000_000)
_TOKEN_KEYS = ('input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens', 'output_tokens')
# API price ratios against an input token: the best public proxy for how much of a usage limit a call takes.
_OUTPUT = 5.0
_WRITE_5M = 1.25
_WRITE_1H = 2.0
# Cache reads got cheaper relative to input with the newer models; matched as a prefix of `message.model`.
_CACHE_READS = {
    'claude-fable-5-1': 0.025,
    'claude-fable-5-2': 0.025,
    'claude-mythos-5-1': 0.025,
    'claude-mythos-5-2': 0.025,
    'claude-opus-5-5': 0.05,
    'claude-sonnet-5-5': 0.05,
}
_CACHE_READ = 0.1
_TTL_5M = timedelta(minutes=5)
_TTL_1H = timedelta(hours=1)


@dataclass(frozen=True)
class Tally:
    turns: int
    tokens: int
    weight: float


@dataclass(frozen=True)
class Charge:
    """One wasted call: its kind, what it was about (redacted), and its cost."""

    kind: str
    evidence: str
    tally: Tally


@dataclass(frozen=True)
class Measurement:
    days: int
    total: Tally
    waste: dict[str, Tally]
    # Calls charged to a kind with text behind it; expired caches and long contexts have none.
    charges: list[Charge] = field(default_factory=list)


@dataclass(frozen=True)
class _Call:
    at: datetime | None
    context: tuple
    model: str | None
    usage: dict


def measure(*, days: int, root: Path, since: datetime) -> Measurement:
    """Returns the turns and tokens spent in the window, and those wasted per kind."""
    calls: dict[str, _Call] = {}
    # Call id -> (kind, evidence); the first charge wins.
    charges: dict[str, tuple[str, str]] = {}
    issuer: dict[str, tuple[str, str]] = {}
    seen: dict[tuple, set[tuple]] = collections.defaultdict(set)
    closing: dict[tuple, str] = {}
    go_ahead: dict[tuple, str] = {}
    for e in transcripts.iter_entries(root=root, since=since, subagents=True):
        # Cowork's audit.jsonl uses the Agent SDK's `session_id`.
        context = (e.get('sessionId') or e.get('session_id'), e.get('agentId'))
        if e.get('isCompactSummary'):
            # Compaction drops what was read, so reading it again is needed.
            seen[context].clear()
        # Condition copied from `iter_turn_ends`: a reply counts only after Claude said something in that session.
        if (text := transcripts.typed_text(e)) and context in closing and transcripts.is_go_ahead(text):
            go_ahead[context] = closing[context]
        message = e.get('message') or {}
        call_id, usage = message.get('id'), message.get('usage')
        is_call = e.get('type') == 'assistant' and isinstance(call_id, str) and isinstance(usage, dict)
        if is_call:
            model = message.get('model')
            calls.setdefault(call_id, _Call(
                at=_timestamp(e),
                context=context,
                model=model if isinstance(model, str) else None,
                usage=usage,
            ))
            if line := transcripts.last_line(transcripts.message_text(e)):
                closing[context] = line
            else:
                closing.setdefault(context, '')
            if context in go_ahead:
                charges.setdefault(call_id, ('bare_yes', go_ahead.pop(context)))
        for block in _blocks(message):
            if block.get('type') == 'tool_use' and is_call:
                tool_input = block.get('input')
                issuer[block.get('id')] = (call_id, f'{block.get("name") or "?"}: {transcripts.attempt(tool_input)}')
                if _is_reread(block, seen=seen[context]):
                    path = tool_input.get('file_path') or tool_input.get('notebook_path')
                    charges.setdefault(call_id, ('reread', path))
            elif block.get('type') == 'tool_result' and block.get('is_error'):
                if (by := issuer.get(block.get('tool_use_id'))) is not None:
                    error = transcripts.error_line(block.get('content'))
                    charges.setdefault(by[0], (_error_kind(error), f'{by[1]} → {error or NO_EVIDENCE}'))
    waste = {kind: _tally([calls[c] for c, (k, _) in charges.items() if k == kind]) for kind in KINDS}
    rebuilt = _rebuilds(calls, charged=charges.keys())
    waste['cache_rebuild'] = Tally(
        turns=len(rebuilt),
        tokens=sum(tokens for tokens, _ in rebuilt.values()),
        weight=sum(weight for _, weight in rebuilt.values()),
    )
    waste['long_context'] = _long_context(
        [call for c, call in calls.items() if c not in charges and c not in rebuilt],
        windows=_windows(calls.values()),
    )
    total = _tally(list(calls.values()))
    _LOGGER.info('Measured `%s` turns, `%s` wasted.', total.turns, sum(t.turns for t in waste.values()))
    charged = [
        Charge(kind=charges[c][0], evidence=redact(charges[c][1] or NO_EVIDENCE), tally=_tally([call]))
        for c, call in calls.items()
        if c in charges
    ]
    return Measurement(days=days, total=total, waste=waste, charges=charged)


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


def _windows(calls) -> dict[tuple, int]:
    """Returns each context's window: the smallest that fits its largest call, since transcripts don't say."""
    largest = collections.defaultdict(int)
    for call in calls:
        largest[call.context] = max(largest[call.context], _context(call.usage))
    return {c: next((w for w in _CONTEXT_WINDOWS if w >= n), _CONTEXT_WINDOWS[-1]) for c, n in largest.items()}


def _rebuilds(calls: dict[str, _Call], *, charged) -> dict[str, tuple[int, float]]:
    """Returns the tokens and extra weight of each uncharged call that rewrote a cache that expired in a gap."""
    # The extra weight is what writing the previous context again cost above reading it from a warm cache.
    by_context = collections.defaultdict(list)
    for call_id, call in calls.items():
        if call.at is not None:
            by_context[call.context].append((call.at, call_id, call))
    rebuilt = {}
    for timed in by_context.values():
        ttl = _ttl([call for _, _, call in timed])
        for (prev_at, _, prev), (at, call_id, call) in pairwise(sorted(timed, key=lambda t: t[0])):
            written = _count(call.usage, 'cache_creation_input_tokens')
            if at - prev_at > ttl and written and call_id not in charged:
                tokens = min(written, _context(prev.usage))
                rebuilt[call_id] = (tokens, tokens * (_write_weight(call.usage) / written - _read_ratio(call.model)))
    return rebuilt


def _ttl(calls: list[_Call]) -> timedelta:
    """Returns a context's cache lifetime: 1 hour if most of its cache writes were 1-hour ones."""
    written = sum(_count(c.usage, 'cache_creation_input_tokens') for c in calls)
    one_hour = sum(_count(_split(c.usage), 'ephemeral_1h_input_tokens') for c in calls)
    return _TTL_1H if one_hour * 2 > written else _TTL_5M


def _timestamp(e: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(e['timestamp'])
    except (KeyError, TypeError, ValueError):
        return None


def _long_context(uncharged: list[_Call], *, windows: dict[tuple, int]) -> Tally:
    """Returns the tally of the context each call carried above half its window."""
    turns, tokens, weight = 0, 0, 0.0
    for call in uncharged:
        if (excess := _context(call.usage) - windows[call.context] // 2) > 0:
            turns, tokens = turns + 1, tokens + excess
            weight += _weight(call) * excess / _context(call.usage)
    return Tally(turns=turns, tokens=tokens, weight=weight)


def _context(usage: dict) -> int:
    return sum(_count(usage, key) for key in ('input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens'))


def _error_kind(error: str) -> str:
    if transcripts.is_denial(error):
        return 'denied'
    return 'refused_edit' if _REFUSED_EDIT in error else 'failed'


def _tally(calls: list[_Call]) -> Tally:
    return Tally(
        turns=len(calls),
        tokens=sum(_count(c.usage, key) for c in calls for key in _TOKEN_KEYS),
        weight=sum(map(_weight, calls)),
    )


def _weight(call: _Call) -> float:
    """Returns how much of a usage limit a call takes, in input-token equivalents."""
    usage = call.usage
    return (
        _count(usage, 'input_tokens')
        + _write_weight(usage)
        + _count(usage, 'cache_read_input_tokens') * _read_ratio(call.model)
        + _count(usage, 'output_tokens') * _OUTPUT
    )


def _write_weight(usage: dict) -> float:
    """Returns the weight of a call's cache writes; a write without the TTL split counts as 5-minute."""
    written = _count(usage, 'cache_creation_input_tokens')
    one_hour = min(_count(_split(usage), 'ephemeral_1h_input_tokens'), written)
    return one_hour * _WRITE_1H + (written - one_hour) * _WRITE_5M


def _split(usage: dict) -> dict:
    """Returns the cache writes split by TTL, which older transcripts don't have."""
    split = usage.get('cache_creation')
    return split if isinstance(split, dict) else {}


def _read_ratio(model: str | None) -> float:
    return next((r for prefix, r in _CACHE_READS.items() if model and model.startswith(prefix)), _CACHE_READ)


def _count(usage: dict, key: str) -> int:
    value = usage.get(key)
    return value if isinstance(value, int) else 0
