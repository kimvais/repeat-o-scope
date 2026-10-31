import io
import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from repeat_o_scope import worklog

_DATA = Path(__file__).parent / 'data'
_NOW = datetime.fromisoformat('2026-10-31T10:00:00.123456+02:00')


@pytest.fixture
def env(tmp_path):
    transcript = tmp_path / 'transcript.jsonl'
    shutil.copy(_DATA / 'transcript.jsonl', transcript)
    return {
        'hook': {'session_id': 's-1', 'transcript_path': str(transcript), 'cwd': str(tmp_path)},
        'log_dir': tmp_path / 'log',
        'state_dir': tmp_path / 'state',
        'transcript': transcript,
    }


def _stop(env, *, now=_NOW, hook=None):
    worklog.record_stop(
        hook=hook or env['hook'],
        log_dir=env['log_dir'],
        now=now,
        state_dir=env['state_dir'],
    )


def _lines(env, day='2026-10-31'):
    path = env['log_dir'] / f'{day}.jsonl'
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def _user_line(text):
    return json.dumps({'type': 'user', 'message': {'role': 'user', 'content': text}}) + '\n'


@pytest.mark.parametrize(
    'secret',
    [
        'glpat-abcdefghij1234567890',
        'GLPAT-abcdefghij1234567890',
        'ghp_' + 'a' * 30,
        'xoxb-123-456-abc',
        'AKIAABCDEFGHIJKLMNOP',
        'sk-ant-' + 'a' * 30,
        'ya29.a0AfH6SMB',
        '-----BEGIN RSA PRIVATE KEY-----',
    ],
)
def test_secret_never_reaches_log_or_state(env, secret):
    env['transcript'].write_text(_user_line(f'here {secret} there'), encoding='utf-8')
    _stop(env)
    written = (env['log_dir'] / '2026-10-31.jsonl').read_text(encoding='utf-8')
    state = (env['state_dir'] / 's-1.json').read_text(encoding='utf-8')
    assert secret not in written, written
    assert secret not in state, state
    assert _lines(env)[-1]['prompts'] == ['here [REDACTED] there']


def test_missing_transcript_writes_nothing(env):
    env['transcript'].unlink()
    _stop(env)
    assert not env['log_dir'].exists()
    assert not env['state_dir'].exists()


def test_missing_session_id_writes_nothing(env):
    _stop(env, hook={**env['hook'], 'session_id': None})
    assert not env['log_dir'].exists()


def test_only_garbage_lines_logs_empty_stretch(env):
    env['transcript'].write_text('not json\n{"type": \n\n', encoding='utf-8')
    _stop(env)
    entry = _lines(env)[-1]
    assert (entry['turns'], entry['tool_uses'], entry['prompts']) == (0, 0, [])


def test_odd_shaped_lines_are_skipped(env):
    odd = [
        '42',
        '["a list"]',
        json.dumps({'type': 'assistant', 'message': {'content': 'plain string'}}),
        json.dumps({'type': 'user', 'message': {'content': None}}),
    ]
    env['transcript'].write_text('\n'.join(odd) + '\n' + _user_line('real'), encoding='utf-8')
    _stop(env)
    entry = _lines(env)[-1]
    assert (entry['turns'], entry['tool_uses'], entry['prompts']) == (1, 0, ['real'])


def test_first_stop_writes_whole_entry(env):
    long_prompt = json.loads(env['transcript'].read_text(encoding='utf-8').splitlines()[-1])
    long_text = long_prompt['message']['content']
    assert len(long_text) > 200, 'fixture prompt must exceed the cut'
    _stop(env)
    [entry] = _lines(env)
    assert entry == {
        '@context': {
            '@vocab': 'urn:repeat-o-scope:worklog#',
            'started': {'@type': 'http://www.w3.org/2001/XMLSchema#dateTime'},
            'last_stop': {'@type': 'http://www.w3.org/2001/XMLSchema#dateTime'},
            'repo': {'@type': '@id'},
            'files': {'@container': '@set'},
            'prompts': {'@container': '@list'},
        },
        '@id': 'urn:claude-code:session:s-1#1',
        '@type': 'WorkStretch',
        'prefix': 's0001.1',
        'index': 1,
        'session_id': 's-1',
        'seq': 1,
        'started': _NOW.isoformat(),
        'last_stop': _NOW.isoformat(),
        'cwd': env['hook']['cwd'],
        'repo': None,
        'branch': 'feature',
        'turns': 3,
        'tool_uses': 5,
        'files': ['C:\\work\\a.py', 'C:\\work\\b.py'],
        'prompts': [
            'Fix the bug',
            'use token [REDACTED] please',
            long_text[:200] + '...',
        ],
        'transcript': str(env['transcript']),
    }


def test_short_gap_reappends_same_stretch_with_totals(env):
    _stop(env)
    with env['transcript'].open('a', encoding='utf-8') as f:
        f.write(_user_line('and the docs'))
    later = _NOW + timedelta(minutes=14)
    _stop(env, now=later)
    first, second = _lines(env)
    assert second['@id'] == first['@id'] == 'urn:claude-code:session:s-1#1'
    assert second['started'] == first['started']
    assert second['last_stop'] == later.isoformat()
    assert second['turns'] == 4
    assert second['prompts'][-1] == 'and the docs'


def test_gap_of_15_minutes_starts_next_stretch(env):
    _stop(env)
    with env['transcript'].open('a', encoding='utf-8') as f:
        f.write(_user_line('next thing'))
    later = _NOW + timedelta(minutes=15)
    _stop(env, now=later)
    second = _lines(env)[-1]
    assert second['@id'] == 'urn:claude-code:session:s-1#2'
    assert second['prefix'] == 's0001.2'
    assert (second['started'], second['turns'], second['prompts']) == (later.isoformat(), 1, ['next thing'])


def test_stretch_stays_in_file_of_its_first_turn(env):
    late = datetime.fromisoformat('2026-10-31T23:55:00+02:00')
    _stop(env, now=late)
    _stop(env, now=late + timedelta(minutes=10))
    assert len(_lines(env)) == 2
    assert not (env['log_dir'] / '2026-11-01.jsonl').exists()


def test_keeps_last_10_prompts_in_log_but_all_in_state(env):
    env['transcript'].write_text(''.join(_user_line(f'p{i}') for i in range(12)), encoding='utf-8')
    _stop(env)
    assert _lines(env)[-1]['prompts'] == [f'p{i}' for i in range(2, 12)]
    state = json.loads((env['state_dir'] / 's-1.json').read_text(encoding='utf-8'))
    assert state['prompts'] == [f'p{i}' for i in range(12)]


def test_sessions_get_consecutive_indexes(env, tmp_path):
    other = tmp_path / 'other.jsonl'
    other.write_text(_user_line('hi'), encoding='utf-8')
    _stop(env)
    _stop(env, hook={'session_id': 's-2', 'transcript_path': str(other), 'cwd': str(tmp_path)})
    assert [e['prefix'] for e in _lines(env)] == ['s0001.1', 's0002.1']


def test_continues_from_powershell_state(env):
    # State as worklog.ps1 wrote it: BOM, 7 fractional digits, Vainu vocabulary.
    env['state_dir'].mkdir()
    started = '2026-10-31T09:50:00.1234567+02:00'
    state = {
        '@id': 'urn:claude-code:session:s-1#3',
        'prefix': 's0494.3',
        'index': 494,
        'session_id': 's-1',
        'seq': 3,
        'started': started,
        'last_stop': '2026-10-31T09:55:00.1234567+02:00',
        'branch': 'main',
        'turns': 2,
        'tool_uses': 7,
        'files': ['C:\\work\\old.py'],
        'prompts': ['earlier'],
        'offset': 12,
    }
    (env['state_dir'] / 's-1.json').write_text(json.dumps(state, indent=2), encoding='utf-8-sig')
    with env['transcript'].open('a', encoding='utf-8') as f:
        f.write(_user_line('one more'))
    _stop(env)
    entry = _lines(env)[-1]
    assert (entry['prefix'], entry['started'], entry['turns'], entry['tool_uses']) == ('s0494.3', started, 3, 7)
    assert entry['prompts'] == ['earlier', 'one more']
    assert entry['files'] == ['C:\\work\\old.py']


def test_main_never_fails_on_bad_input(monkeypatch):
    monkeypatch.setattr('sys.stdin', io.StringIO('not json'))
    worklog.main()
