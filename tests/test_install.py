import json

import pytest

from repeat_o_scope import install
from repeat_o_scope.findings import RepeatOScopeError

_OURS = {'hooks': [{'type': 'command', 'command': 'repeat-o-scope-worklog'}]}


def _settings(claude_dir):
    return json.loads((claude_dir / 'settings.json').read_text(encoding='utf-8'))


def _skill(claude_dir):
    return claude_dir / 'skills' / 'repeat-o-scope' / 'SKILL.md'


def test_unparsable_settings_raises_and_writes_nothing(tmp_path):
    path = tmp_path / 'settings.json'
    path.write_bytes(b'{ // comment\n }')
    with pytest.raises(RepeatOScopeError, match='settings.json'):
        install.install(claude_dir=tmp_path)
    assert path.read_bytes() == b'{ // comment\n }'
    assert not (tmp_path / 'skills').exists()


def test_install_into_empty_claude_dir(tmp_path):
    install.install(claude_dir=tmp_path)
    assert _settings(tmp_path) == {'hooks': {'Stop': [_OURS]}}
    assert _skill(tmp_path).read_text(encoding='utf-8').startswith('---')


def test_install_twice_is_idempotent(tmp_path):
    install.install(claude_dir=tmp_path)
    once = (tmp_path / 'settings.json').read_bytes()
    assert install.install(claude_dir=tmp_path) == []
    assert (tmp_path / 'settings.json').read_bytes() == once


def test_install_replaces_worklog_ps1_and_keeps_other_hooks(tmp_path):
    session_start = {'matcher': 'startup', 'hooks': [{'type': 'command', 'command': 'probe.ps1'}]}
    other_stop = {'type': 'command', 'command': 'notify.ps1'}
    (tmp_path / 'settings.json').write_text(
        json.dumps({
            'model': 'x',
            'hooks': {
                'SessionStart': [session_start],
                'Stop': [
                    {'hooks': [{'type': 'command', 'command': 'pwsh -NoProfile -File C:/Users/k/.claude/hooks/worklog.ps1'}]},
                    {'hooks': [other_stop]},
                ],
            },
        }),
        encoding='utf-8-sig',
    )
    changes = install.install(claude_dir=tmp_path)
    assert _settings(tmp_path) == {
        'model': 'x',
        'hooks': {'SessionStart': [session_start], 'Stop': [{'hooks': [other_stop]}, _OURS]},
    }
    assert any('worklog.ps1' in c for c in changes), changes
    assert (tmp_path / 'settings.json.repeat-o-scope.bak').exists()


def test_uninstall_reverses_install(tmp_path):
    (tmp_path / 'settings.json').write_text(json.dumps({'model': 'x'}), encoding='utf-8')
    install.install(claude_dir=tmp_path)
    install.uninstall(claude_dir=tmp_path)
    assert _settings(tmp_path) == {'model': 'x'}
    assert not _skill(tmp_path).parent.exists()
