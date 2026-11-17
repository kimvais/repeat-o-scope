"""Checks a Claude Code config against the budget checklist in docs/why.md."""

import dataclasses
import json
import logging
from pathlib import Path

from repeat_o_scope.install import load_settings

_LOGGER = logging.getLogger(__name__)

CHECKS = ('effort', 'skill_overrides', 'skills', 'status_line', 'scope_rule', 'read_once_rule')
_LOW_EFFORT = {'low', 'medium'}
# docs/why.md gives no number; above this, skill descriptions are a noticeable fixed cost per turn.
_MAX_SKILLS = 30


@dataclasses.dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    found: str
    fix: str


def diagnose(*, claude_dir: Path, project_dir: Path) -> list[Check]:
    """Returns one check per `CHECKS` name, failing ones first."""
    layers = _layers(claude_dir=claude_dir, project_dir=project_dir)
    done = {
        'effort': _effort(layers),
        'skill_overrides': _skill_overrides(layers),
        'skills': _skills(claude_dir=claude_dir, layers=layers, project_dir=project_dir),
    }
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


def _skill_overrides(layers: list[tuple[Path, dict]]) -> Check:
    off = {name for name, state in _merged(layers, key='skillOverrides').items() if state == 'off'}
    source = next((path for path, settings in layers if settings.get('skillOverrides')), None)
    if not off:
        return Check('skill_overrides', False, 'not set', 'Turn off skills you never use with skillOverrides in settings.json.')
    return Check('skill_overrides', True, f'{len(off)} skills off ({_shown(source)})', '')


def _skills(*, claude_dir: Path, layers: list[tuple[Path, dict]], project_dir: Path) -> Check:
    names = [p.parent.name for d in (claude_dir / 'skills', project_dir / '.claude' / 'skills') for p in d.glob('*/SKILL.md')]
    enabled = {key for key, on in _merged(layers, key='enabledPlugins').items() if on is True}
    for key, install_path in _install_paths(claude_dir).items():
        if key in enabled:
            plugin = key.split('@')[0]
            names += [f'{plugin}:{p.parent.name}' for p in Path(install_path).glob('skills/*/SKILL.md')]
    off = {name for name, state in _merged(layers, key='skillOverrides').items() if state == 'off'}
    # An override names a skill bare (`plan`) or with its plugin (`sp:plan`).
    count = sum(name not in off and name.split(':')[-1] not in off for name in names)
    # Built-in and claude.ai skills are not on disk, so the real number is higher.
    found = f'at least {count} enabled'
    if count > _MAX_SKILLS:
        return Check('skills', False, found, "Turn off skills you never use: every enabled skill's description is in context on every turn.")
    return Check('skills', True, found, '')


def _install_paths(claude_dir: Path) -> dict[str, str]:
    """Returns each installed plugin's `installPath`; none when the file is missing or unreadable."""
    path = claude_dir / 'plugins' / 'installed_plugins.json'
    try:
        plugins = json.loads(path.read_text(encoding='utf-8-sig'))['plugins']
        return {key: entries[0]['installPath'] for key, entries in plugins.items() if entries}
    except (OSError, ValueError, KeyError, TypeError, IndexError) as err:
        # Plugins are optional: a missing or odd file means no plugin skills, not a broken audit.
        _LOGGER.debug('No plugin skills from `%s`: %s', path, err)
        return {}


def _merged(layers: list[tuple[Path, dict]], *, key: str) -> dict:
    """Returns the dict setting `key` merged over all layers, higher precedence winning per entry."""
    merged = {}
    for _, settings in reversed(layers):
        if isinstance(settings.get(key), dict):
            merged |= settings[key]
    return merged


def _shown(path: Path) -> str:
    try:
        return str(Path('~') / path.relative_to(Path.home()))
    except ValueError:
        return str(path)
