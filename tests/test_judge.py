from types import SimpleNamespace

import pytest

from repeat_o_scope import cli, judge, transcripts as t


class _FakeJev:
    """Stands in for the TypeSafe API: answers from keywords in the state, and records every call."""

    def __init__(self):
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def system_one(self, *, state, questions):
        self.calls.append((state, questions))
        if 'closing' in state:
            kind = 'permission' if 'ship' in state['closing'] else 'report'
            return SimpleNamespace(choices={'kind': SimpleNamespace(choice=kind)}, nouls={})
        real = 'warning' not in state['error']
        return SimpleNamespace(choices={}, nouls={'real': SimpleNamespace(noul=0.9 if real else 0.1)})


@pytest.fixture
def jev(monkeypatch):
    fake = _FakeJev()
    monkeypatch.setattr(judge, 'make_client', lambda: fake)
    return fake


def test_without_jev_flag_no_client_is_made(transcripts, monkeypatch):
    def refuse():
        raise AssertionError('Jev must not be called without --jev')

    monkeypatch.setattr(judge, 'make_client', refuse)
    transcripts.write([
        transcripts.assistant([{'type': 'text', 'text': 'Say "ship" and I will push.'}]),
        transcripts.user('ship'),
    ])
    cli.Scope(root=str(transcripts.root)).asks()


def test_kinds_of_asks_one_question_per_closing(jev):
    kinds = judge.kinds_of_asks(['Say "ship" and I will push.', 'The commit says Refs #188.'])
    assert kinds == ['permission', 'report']
    state, questions = jev.calls[0]
    assert state == {'closing': 'Say "ship" and I will push.'}
    assert set(questions['kind'].criteria) == {'permission', 'choice', 'clarifying', 'review', 'report'}


def test_real_failures_keeps_only_real_ones(jev):
    failures = [
        t.ToolFailure(tool='Edit', attempt='C:\\a.py', error='File has not been read yet.'),
        t.ToolFailure(tool='PowerShell', attempt='git add .', error='warning: LF will be replaced by CRLF'),
    ]
    assert judge.real_failures(failures) == [True, False]
    assert jev.calls[1][0] == {'tool': 'PowerShell', 'attempt': 'git add .', 'error': 'warning: LF will be replaced by CRLF'}


def test_asks_report_groups_closings_by_kind(transcripts, jev):
    entries = []
    for closing in ['Say "ship" and I will push.'] * 2 + ['Done, all tests pass.']:
        entries += [transcripts.assistant([{'type': 'text', 'text': closing}]), transcripts.user('ship')]
    transcripts.write(entries)
    report = cli.Scope(root=str(transcripts.root), jev=True).asks()
    assert '## permission: 2' in report, report
    assert '## report: 1' in report, report
    assert len(jev.calls) == 2, 'one call per distinct closing'


def test_fails_report_drops_noise_groups(transcripts, jev):
    entries = []
    errors = ['File has not been read yet.'] * 4 + ['warning: LF will be replaced by CRLF'] * 4
    for i, error in enumerate(errors):
        entries += [
            transcripts.assistant([{'type': 'tool_use', 'id': f'u{i}', 'name': 'Edit', 'input': {'file_path': f'C:\\{i}.py'}}]),
            transcripts.user([{'type': 'tool_result', 'tool_use_id': f'u{i}', 'is_error': True, 'content': error}]),
        ]
    transcripts.write(entries)
    report = cli.Scope(root=str(transcripts.root), jev=True).fails()
    failures = report.split('## Denied')[0]
    assert 'file has not been read yet' in failures, report
    assert 'crlf' not in failures, report
    assert 'Jev judged 1 group(s) as noise' in failures, report
