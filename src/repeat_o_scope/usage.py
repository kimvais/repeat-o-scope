"""Counts sessions, turns and tokens per model."""

import collections
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from repeat_o_scope import transcripts, waste

_LOGGER = logging.getLogger(__name__)

UNKNOWN_MODEL = '(unknown)'
# Messages Claude Code writes itself (an API error, an interruption): they carry usage but are no API call.
_SYNTHETIC = '<synthetic>'


@dataclass(frozen=True)
class Row:
    model: str
    sessions: int
    turns: int
    input: int
    cache_write: int
    cache_read: int
    output: int
    # How much of a usage limit the calls took, in input-token equivalents.
    weight: float

    @property
    def tokens(self) -> int:
        return self.input + self.cache_write + self.cache_read + self.output


@dataclass(frozen=True)
class Usage:
    days: int
    # Heaviest first.
    models: list[Row]
    total: Row


def count(*, days: int, root: Path, since: datetime) -> Usage:
    """Returns the sessions, turns and tokens in the window, per model and in total."""
    calls: dict[str, waste.Call] = {}
    for e in transcripts.iter_entries(progress=True, root=root, since=since, subagents=True):
        if (call := waste.as_call(e)) is not None and call.model != _SYNTHETIC:
            calls.setdefault(call.id, call)
    by_model = collections.defaultdict(list)
    for call in calls.values():
        by_model[call.model or UNKNOWN_MODEL].append(call)
    models = sorted((_row(m, cs) for m, cs in by_model.items()), key=lambda r: -r.weight)
    total = _row('Total', list(calls.values()))
    _LOGGER.info('Counted `%s` sessions and `%s` turns.', total.sessions, total.turns)
    return Usage(days=days, models=models, total=total)


def _row(model: str, calls: list[waste.Call]) -> Row:
    def tokens(key: str) -> int:
        return sum(waste.token_count(c.usage, key) for c in calls)

    return Row(
        model=model,
        # A subagent runs under its parent's session id, so it is not a session of its own.
        sessions=len({c.context[0] for c in calls}),
        turns=len(calls),
        input=tokens('input_tokens'),
        cache_write=tokens('cache_creation_input_tokens'),
        cache_read=tokens('cache_read_input_tokens'),
        output=tokens('output_tokens'),
        weight=sum(map(waste.call_weight, calls)),
    )
