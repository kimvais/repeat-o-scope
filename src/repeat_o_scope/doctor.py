"""Checks a Claude Code config against the budget checklist in docs/why.md."""

import dataclasses
import logging
from pathlib import Path

from repeat_o_scope.install import load_settings

_LOGGER = logging.getLogger(__name__)

CHECKS = ('effort', 'skill_overrides', 'skills', 'status_line', 'scope_rule', 'read_once_rule')
_LOW_EFFORT = {'low', 'medium'}


@dataclasses.dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    found: str
    fix: str


def diagnose(*, claude_dir: Path, project_dir: Path) -> list[Check]:
    """Returns one check per `CHECKS` name, failing ones first."""
    layers = _layers(claude_dir=claude_dir, project_dir=project_dir)
    done = {'effort': _effort(layers)}
    checks = [done.get(name, Check(name, False, 'not checked yet', '')) for name in CHECKS]
    checks.sort(key=lambda c: c.ok)
    _LOGGER.info('Checked %s settings, %s to fix.', len(checks), sum(not c.ok for c in checks))
    return checks


def _layers(*, claude_dir: Path, project_dir: Path) -> list[tuple[Path, dict]]:
    """Returns the settings files that exist, highest precedence first."""
    paths = [
        project_dir / '.claude' / 'settings.local.json',
        project_dir / '.claude' / 'settings.json',
        claude_dir / 'settings.json',
    ]
    return [(p, load_settings(p)) for p in paths if p.exists()]


def _effort(layers: list[tuple[Path, dict]]) -> Check:
    for path, settings in layers:
        levels = [settings.get('effortLevel')] + [
            m.get('effortLevel') for m in settings.get('modelSettings', {}).values() if isinstance(m, dict)
        ]
        levels = sorted({str(level) for level in levels if level})
        if levels:
            ok = set(levels) <= _LOW_EFFORT
            fix = '' if ok else 'Set effort to medium in /config.'
            return Check('effort', ok, f'{", ".join(levels)} ({_shown(path)})', fix)
    return Check('effort', False, 'not set', 'Set effort to medium in /config.')


def _shown(path: Path) -> str:
    try:
        return str(Path('~') / path.relative_to(Path.home()))
    except ValueError:
        return str(path)
