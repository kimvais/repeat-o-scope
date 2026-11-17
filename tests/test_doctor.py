import json

import pytest

from repeat_o_scope import doctor
from repeat_o_scope.doctor import Check
from repeat_o_scope.findings import RepeatOScopeError


@pytest.fixture(autouse=True)
def _home_elsewhere(monkeypatch):
    # tmp_path sits under the real home on Windows; `~` shortening would hide the paths the tests compare.
    monkeypatch.setenv('USERPROFILE', 'Z:\\nohome')
    monkeypatch.setenv('HOME', 'Z:\\nohome')


@pytest.fixture
def dirs(tmp_path):
    claude_dir, project_dir = tmp_path / 'claude', tmp_path / 'project'
    claude_dir.mkdir()
    project_dir.mkdir()
    return claude_dir, project_dir


def _settings(path, data, *, encoding='utf-8'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding=encoding)
    return path


def _checks(dirs) -> dict[str, Check]:
    claude_dir, project_dir = dirs
    return {c.name: c for c in doctor.diagnose(claude_dir=claude_dir, project_dir=project_dir)}


def test_fresh_install_fails_every_check(dirs):
    checks = doctor.diagnose(claude_dir=dirs[0], project_dir=dirs[1])
    assert [c.name for c in checks] == list(doctor.CHECKS), checks
    assert not any(c.ok for c in checks), checks
    assert all(c.fix for c in checks if c.name == 'effort'), checks


def test_broken_settings_raise(dirs):
    path = dirs[0] / 'settings.json'
    path.write_text('{', encoding='utf-8')
    with pytest.raises(RepeatOScopeError, match='settings.json'):
        doctor.diagnose(claude_dir=dirs[0], project_dir=dirs[1])


def test_bom_settings_parse(dirs):
    _settings(dirs[0] / 'settings.json', {'effortLevel': 'medium'}, encoding='utf-8-sig')
    assert _checks(dirs)['effort'].ok


def test_effort_medium_per_model_passes(dirs):
    path = _settings(
        dirs[0] / 'settings.json',
        {'model': 'opus', 'modelSettings': {'claude-opus-5-5': {'effortLevel': 'medium'}}},
    )
    assert _checks(dirs)['effort'] == Check('effort', True, f'medium ({path})', '')


def test_effort_high_fails(dirs):
    path = _settings(dirs[0] / 'settings.json', {'modelSettings': {'claude-opus-5-5': {'effortLevel': 'high'}}})
    assert _checks(dirs)['effort'] == Check('effort', False, f'high ({path})', 'Set effort to medium in /config.')


def test_local_settings_override_user(dirs):
    _settings(dirs[0] / 'settings.json', {'modelSettings': {'claude-opus-5-5': {'effortLevel': 'medium'}}})
    local = _settings(dirs[1] / '.claude' / 'settings.local.json', {'effortLevel': 'high'})
    effort = _checks(dirs)['effort']
    assert (effort.ok, effort.found) == (False, f'high ({local})'), effort
