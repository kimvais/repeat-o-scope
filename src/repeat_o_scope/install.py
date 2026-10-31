"""Installs the repeat-o-scope skill and the worklog Stop hook into a Claude Code config directory."""

import json
import logging
import re
import shutil
from importlib import resources
from pathlib import Path

from repeat_o_scope.findings import RepeatOScopeError

_LOGGER = logging.getLogger(__name__)

_HOOK = {'type': 'command', 'command': 'repeat-o-scope-worklog'}
# The PowerShell hook this package replaces: two loggers would log every stretch twice.
# A whole file name only, so a user's own `my-team-worklog.ps1` survives.
_REPLACED = re.compile(r'(^|[\\/\s"\'])worklog\.ps1\b')
_BACKUP_SUFFIX = '.repeat-o-scope.bak'


def install(*, claude_dir: Path) -> list[str]:
    """Copies the skill and registers the Stop hook; returns what changed."""
    settings_path = claude_dir / 'settings.json'
    settings = _load(settings_path)
    stop = settings.setdefault('hooks', {}).setdefault('Stop', [])
    changes = []
    for group in stop:
        kept = [h for h in group.get('hooks', []) if not _REPLACED.search(h.get('command', ''))]
        if len(kept) != len(group.get('hooks', [])):
            changes.append(f'Removed the `worklog.ps1` Stop hook from {settings_path}.')
            group['hooks'] = kept
    stop[:] = [g for g in stop if g.get('hooks')]
    if not any(_is_ours(h) for g in stop for h in g['hooks']):
        stop.append({'hooks': [dict(_HOOK)]})
        changes.append(f'Added the `{_HOOK["command"]}` Stop hook to {settings_path}.')
    if changes:
        _save(settings_path, settings)

    skill = _skill_path(claude_dir)
    source = resources.files('repeat_o_scope').joinpath('skill', 'SKILL.md').read_text(encoding='utf-8')
    if not skill.exists() or skill.read_text(encoding='utf-8') != source:
        skill.parent.mkdir(parents=True, exist_ok=True)
        skill.write_text(source, encoding='utf-8')
        changes.append(f'Wrote the skill to {skill}.')
    for change in changes:
        _LOGGER.info('%s', change)
    return changes


def uninstall(*, claude_dir: Path) -> list[str]:
    """Removes the Stop hook and the skill; returns what changed."""
    settings_path = claude_dir / 'settings.json'
    settings = _load(settings_path)
    changes = []
    hooks = settings.get('hooks', {})
    stop = hooks.get('Stop', [])
    for group in stop:
        if any(_is_ours(h) for h in group.get('hooks', [])):
            group['hooks'] = [h for h in group['hooks'] if not _is_ours(h)]
            changes.append(f'Removed the `{_HOOK["command"]}` Stop hook from {settings_path}.')
    stop[:] = [g for g in stop if g.get('hooks')]
    if 'Stop' in hooks and not stop:
        del hooks['Stop']
    if 'hooks' in settings and not hooks:
        del settings['hooks']
    if changes:
        _save(settings_path, settings)
    skill = _skill_path(claude_dir)
    if skill.parent.exists():
        shutil.rmtree(skill.parent)
        changes.append(f'Removed the skill from {skill.parent}.')
    return changes


def _is_ours(hook: dict) -> bool:
    # By command, not the whole entry: a user may have added `timeout` or other keys to it.
    return hook.get('command') == _HOOK['command']


def _skill_path(claude_dir: Path) -> Path:
    return claude_dir / 'skills' / 'repeat-o-scope' / 'SKILL.md'


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        settings = json.loads(path.read_text(encoding='utf-8-sig'))
    except json.JSONDecodeError as err:
        # Never rewrite a file we cannot parse: the user's other settings would be lost.
        raise RepeatOScopeError(f'Cannot parse {path}, leaving it untouched: {err}!') from err
    if not isinstance(settings, dict):
        raise RepeatOScopeError(f'Unexpected `{type(settings).__name__}` at the top of {path}!')
    return settings


def _save(path: Path, settings: dict) -> None:
    backup = path.with_name(path.name + _BACKUP_SUFFIX)
    if path.exists() and not backup.exists():
        shutil.copy2(path, backup)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
