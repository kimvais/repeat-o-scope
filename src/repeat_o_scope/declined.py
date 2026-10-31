"""Remembers findings the user chose to leave alone, so they are not suggested again."""

import json
import logging
import os
import threading
from pathlib import Path

import platformdirs

from repeat_o_scope.findings import Finding, RepeatOScopeError, Report

_LOGGER = logging.getLogger(__name__)

_LAST_PREFIX = 'last-'
_DECLINED = 'declined.json'


def default_dir() -> Path:
    """Returns the local, never-synced directory for repeat-o-scope's state."""
    return platformdirs.user_data_path('repeat-o-scope', appauthor=False)


def remember_last(*, data_dir: Path, report: Report) -> None:
    """Replaces the record of `report`'s last run, so `decline` can look its findings up by id."""
    # One file per report: the skill runs the four reports in parallel, and they must not share a file.
    _save(data_dir / f'{_LAST_PREFIX}{report.name}.json', {f.id: [v.text for v in f.variants] for f in report.findings})


def decline(*, data_dir: Path, finding_id: str) -> list[str]:
    """Marks a finding from the last run of any report as declined and returns its variants."""
    for path in sorted(data_dir.glob(f'{_LAST_PREFIX}*.json')):
        # A cache: a half-written or corrupt one only means nothing is known from that run.
        if (variants := _load(path, lenient=True).get(finding_id)) is not None:
            break
    else:
        raise RepeatOScopeError(f'Unknown finding `{finding_id}`: not in the last run of any report!')
    declined = _load(data_dir / _DECLINED, lenient=False)
    declined[finding_id] = variants
    _save(data_dir / _DECLINED, declined)
    _LOGGER.info('Declined `%s`.', finding_id)
    return variants


def drop_declined(*, data_dir: Path, findings: list[Finding]) -> list[Finding]:
    """Returns `findings` without those whose top variant was in a declined finding."""
    # Variants, not ids: as the window slides, a group's top variant and its boundaries shift.
    gone = {text for variants in _load(data_dir / _DECLINED, lenient=False).values() for text in variants}
    return [f for f in findings if f.variants[0].text not in gone]


def _load(path: Path, *, lenient: bool) -> dict[str, list[str]]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError) as err:
        if lenient:
            _LOGGER.warning('Ignoring unreadable `%s`.', path)
            return {}
        # The user's decisions: never silently forget them.
        raise RepeatOScopeError(f'Cannot read {path}; fix or delete it: {err}!') from err


def _save(path: Path, data: dict[str, list[str]]) -> None:
    """Writes `data` atomically, so a concurrent reader never sees a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f'{path.name}.{os.getpid()}.{threading.get_ident()}.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, path)
