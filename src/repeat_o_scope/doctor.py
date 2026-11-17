"""Checks a Claude Code config against the budget checklist in docs/why.md."""

import dataclasses
import json
import logging
import os
import re
from pathlib import Path

from repeat_o_scope.install import load_settings

_LOGGER = logging.getLogger(__name__)

CHECKS = (
    'effort',
    'skill_overrides',
    'skills',
    'status_line',
    'scope_rule',
    'read_once_rule',
    'idle_compact',
    'autocompact_override',
)
# The compaction checks follow https://github.com/inevolin/claude-code-usage-analysis.
_IDLE_COMPACT = 'CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS'
_IDLE_COMPACT_MAX = 100_000
_AUTOCOMPACT_PCT = 'CLAUDE_AUTOCOMPACT_PCT_OVERRIDE'
_LOW_EFFORT = {'low', 'medium'}
# docs/why.md gives no number; above this, skill descriptions are a noticeable fixed cost per turn.
_MAX_SKILLS = 30
# The two rules docs/why.md says to copy first, matched by their key phrases; the fix is the rule's text there.
_RULES = {
    'scope_rule': (
        re.compile(r'whole scope|out of scope|\(OOS\)', re.IGNORECASE),
        'Add to CLAUDE.md: "The issue is the whole scope. Repo rules shape the code but never add work; '
        "anything extra goes in the summary as 'left out (OOS)'.\"",
    ),
    'read_once_rule': (
        re.compile(r'read,? edit,? read,? edit|read (?:\w+ ){0,3}once|in one pass', re.IGNORECASE),
        'Add to CLAUDE.md: "No read, edit, read, edit loop. List every file needed, read them in one pass, '
        'make all edits, run the tests once."',
    ),
}


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
        'status_line': _status_line(layers),
        'idle_compact': _idle_compact(layers),
        'autocompact_override': _autocompact_override(layers),
    }
    claude_mds = [claude_dir / 'CLAUDE.md', project_dir / 'CLAUDE.md', project_dir / '.claude' / 'CLAUDE.md']
    texts = [(p, p.read_text(encoding='utf-8-sig', errors='replace')) for p in claude_mds if p.exists()]
    for name, (pattern, fix) in _RULES.items():
        where = next((p for p, text in texts if pattern.search(text)), None)
        done[name] = Check(name, True, _shown(where), '') if where else Check(name, False, 'not found', fix)
    checks = [done[name] for name in CHECKS]
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


def _status_line(layers: list[tuple[Path, dict]]) -> Check:
    for path, settings in layers:
        status_line = settings.get('statusLine')
        if isinstance(status_line, dict) and status_line.get('command'):
            # The file, not the command: a command line may carry a token.
            return Check('status_line', True, _shown(path), '')
    return Check('status_line', False, 'not set', 'Add context % and usage % to your status line.')


def _idle_compact(layers: list[tuple[Path, dict]]) -> Check:
    # Idle compact runs only below the 200,000 default if this is lowered; it needs the 1-hour cache of a subscription.
    fix = f'If you take long breaks with the terminal open, set {_IDLE_COMPACT}={_IDLE_COMPACT_MAX} in settings.json env.'
    if (env := _env(layers, name=_IDLE_COMPACT)) is None:
        return Check('idle_compact', False, 'not set', fix)
    value, source = env
    ok = value is not None and value <= _IDLE_COMPACT_MAX
    return Check('idle_compact', ok, f'{"set" if value is None else value} ({source})', '' if ok else fix)


def _autocompact_override(layers: list[tuple[Path, dict]]) -> Check:
    if (env := _env(layers, name=_AUTOCOMPACT_PCT)) is None:
        return Check('autocompact_override', True, 'not set', '')
    value, source = env
    model = next((s['model'] for _, s in layers if isinstance(s.get('model'), str)), '')
    # `[1m]` is how Claude Code names a 1M-context model; anything else is taken as 200k.
    ok = model.lower().endswith('[1m]')
    fix = (
        f'Remove {_AUTOCOMPACT_PCT}: on a 200k-context model it compacts below what a summary leaves, '
        'and the session thrashes.'
    )
    return Check('autocompact_override', ok, f'{"set" if value is None else value} ({source})', '' if ok else fix)


def _env(layers: list[tuple[Path, dict]], *, name: str) -> tuple[int | None, str] | None:
    """Returns a variable's value, `None` unless it is an int, and where it was set; `None` if unset."""
    # Only an int is shown: `env` holds secrets, and a value that isn't a number may be one.
    for path, settings in layers:
        env = settings.get('env')
        if isinstance(env, dict) and name in env:
            return _int(env[name]), _shown(path)
    if name in os.environ:
        return _int(os.environ[name]), 'environment'
    return None


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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
