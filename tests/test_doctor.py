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
    assert sorted(c.name for c in checks) == sorted(doctor.CHECKS), checks
    # No skills at all is under the limit: there is nothing to turn off.
    assert [c.name for c in checks if c.ok] == ['skills'], checks
    assert checks[-1].name == 'skills', checks
    assert all(c.fix for c in checks if not c.ok), checks


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


def _skill(directory, name):
    path = directory / name / 'SKILL.md'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'---\nname: {name}\n---\n', encoding='utf-8')


def _plugins(claude_dir, plugins: dict[str, tuple]):
    """Writes `installed_plugins.json` and returns `enabledPlugins` for `{key: (install_path, enabled)}`."""
    installed = {
        'version': 2,
        'plugins': {key: [{'scope': 'user', 'installPath': str(path)}] for key, (path, _) in plugins.items()},
    }
    _settings(claude_dir / 'plugins' / 'installed_plugins.json', installed)
    return {key: enabled for key, (_, enabled) in plugins.items()}


def _skill_setup(dirs, *, overrides=None):
    claude_dir, project_dir = dirs
    for name in ('mine', 'other'):
        _skill(claude_dir / 'skills', name)
    _skill(project_dir / '.claude' / 'skills', 'repo')
    cache = claude_dir / 'plugins' / 'cache'
    for name in ('plan', 'debug', 'review'):
        _skill(cache / 'sp' / '2.0' / 'skills', name)
        _skill(cache / 'sp' / '1.0' / 'skills', name)
    for i in range(5):
        _skill(cache / 'off' / '1.0' / 'skills', f'off{i}')
    enabled = _plugins(claude_dir, {'sp@market': (cache / 'sp' / '2.0', True), 'off@market': (cache / 'off' / '1.0', False)})
    _settings(claude_dir / 'settings.json', {'enabledPlugins': enabled} | ({'skillOverrides': overrides} if overrides else {}))


def test_no_overrides_fails(dirs):
    assert _checks(dirs)['skill_overrides'] == Check(
        'skill_overrides', False, 'not set', 'Turn off skills you never use with skillOverrides in settings.json.'
    )


def test_overrides_off_pass(dirs):
    path = _settings(dirs[0] / 'settings.json', {'skillOverrides': {'html-report': 'off', 'vjuu': 'off'}})
    assert _checks(dirs)['skill_overrides'] == Check('skill_overrides', True, f'2 skills off ({path})', '')


def test_skills_counted_from_user_project_and_enabled_plugins(dirs):
    _skill_setup(dirs)
    assert _checks(dirs)['skills'] == Check('skills', True, 'at least 6 enabled', '')


def test_overridden_skills_are_not_counted(dirs):
    _skill_setup(dirs, overrides={'mine': 'off', 'sp:plan': 'off'})
    assert _checks(dirs)['skills'].found == 'at least 4 enabled'


def test_more_than_max_skills_fails(dirs):
    for i in range(31):
        _skill(dirs[0] / 'skills', f's{i}')
    assert _checks(dirs)['skills'] == Check(
        'skills',
        False,
        'at least 31 enabled',
        "Turn off skills you never use: every enabled skill's description is in context on every turn.",
    )


_SCOPE_FIX = (
    'Add to CLAUDE.md: "The issue is the whole scope. Repo rules shape the code but never add work; '
    "anything extra goes in the summary as 'left out (OOS)'.\""
)
_READ_ONCE_FIX = (
    'Add to CLAUDE.md: "No read, edit, read, edit loop. List every file needed, read them in one pass, '
    'make all edits, run the tests once."'
)


def test_no_status_line_fails(dirs):
    assert _checks(dirs)['status_line'] == Check(
        'status_line', False, 'not set', 'Add context % and usage % to your status line.'
    )


def test_status_line_command_passes(dirs):
    path = _settings(dirs[0] / 'settings.json', {'statusLine': {'type': 'command', 'command': 'pwsh -File s.ps1'}})
    assert _checks(dirs)['status_line'] == Check('status_line', True, str(path), '')


def test_rules_missing_fail(dirs):
    (dirs[0] / 'CLAUDE.md').write_text('Use uv.', encoding='utf-8')
    checks = _checks(dirs)
    assert checks['scope_rule'] == Check('scope_rule', False, 'not found', _SCOPE_FIX)
    assert checks['read_once_rule'] == Check('read_once_rule', False, 'not found', _READ_ONCE_FIX)


@pytest.mark.parametrize('where', ['user', 'project', 'project_dot_claude'])
@pytest.mark.parametrize(
    ('text', 'name'),
    [
        ('The issue or request is the whole scope.', 'scope_rule'),
        ('Anything else is left out (OOS).', 'scope_rule'),
        ('No read, edit, read, edit loop.', 'read_once_rule'),
        ('List every file, read them in one pass.', 'read_once_rule'),
        ('Read once, edit once, test once.', 'read_once_rule'),
    ],
)
def test_rules_found_in_any_claude_md(dirs, where, text, name):
    claude_dir, project_dir = dirs
    path = {
        'user': claude_dir / 'CLAUDE.md',
        'project': project_dir / 'CLAUDE.md',
        'project_dot_claude': project_dir / '.claude' / 'CLAUDE.md',
    }[where]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'# Rules\n{text}\n', encoding='utf-8')
    assert _checks(dirs)[name] == Check(name, True, str(path), '')


def test_rule_words_apart_do_not_match(dirs):
    (dirs[0] / 'CLAUDE.md').write_text('Read the docs. Scope it down. Do it once.', encoding='utf-8')
    checks = _checks(dirs)
    assert (checks['scope_rule'].ok, checks['read_once_rule'].ok) == (False, False), checks


def test_local_settings_override_user(dirs):
    _settings(dirs[0] / 'settings.json', {'modelSettings': {'claude-opus-5-5': {'effortLevel': 'medium'}}})
    local = _settings(dirs[1] / '.claude' / 'settings.local.json', {'effortLevel': 'high'})
    effort = _checks(dirs)['effort']
    assert (effort.ok, effort.found) == (False, f'high ({local})'), effort
