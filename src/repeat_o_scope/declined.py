"""Remembers findings the user chose to leave alone, so they are not suggested again."""

import json
import logging
from pathlib import Path

import platformdirs

from repeat_o_scope.findings import Finding, RepeatOScopeError, Report

_LOGGER = logging.getLogger(__name__)

_LAST = 'last-findings.json'
_DECLINED = 'declined.json'


def default_dir() -> Path:
    """Returns the local, never-synced directory for repeat-o-scope's state."""
    return platformdirs.user_data_path('repeat-o-scope', appauthor=False)


def remember_last(*, data_dir: Path, report: Report) -> None:
    """Records the variants of every finding in `report`, so `decline` can look them up by id."""
    last = _load(data_dir / _LAST)
    last.update({f.id: [v.text for v in f.variants] for f in report.findings})
    _save(data_dir / _LAST, last)


def decline(*, data_dir: Path, finding_id: str) -> list[str]:
    """Marks a finding from the last run as declined and returns its variants."""
    variants = _load(data_dir / _LAST).get(finding_id)
    if variants is None:
        raise RepeatOScopeError(f'Unknown finding `{finding_id}`: run a report first!')
    declined = _load(data_dir / _DECLINED)
    declined[finding_id] = variants
    _save(data_dir / _DECLINED, declined)
    _LOGGER.info('Declined `%s`.', finding_id)
    return variants


def drop_declined(*, data_dir: Path, findings: list[Finding]) -> list[Finding]:
    """Returns `findings` without those whose top variant was in a declined finding."""
    # Variants, not ids: as the window slides, a group's top variant and its boundaries shift.
    gone = {text for variants in _load(data_dir / _DECLINED).values() for text in variants}
    return [f for f in findings if f.variants[0].text not in gone]


def _load(path: Path) -> dict[str, list[str]]:
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def _save(path: Path, data: dict[str, list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
