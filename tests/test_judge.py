import pytest

from repeat_o_scope import cli, judge, judges
from repeat_o_scope.judges import Answer


class _FakeJudge(judges.Judge):
    """Stands in for a judge's API: answers from keywords in the state, and records every call."""

    name = 'fake'
    env = ()

    def __init__(self):
        self.calls = []

    async def _ask(self, *, questions, state):
        self.calls.append((state, questions))
        if 'closing' in state:
            return {'kind': Answer(choice='permission' if 'ship' in state['closing'] else 'report', noul=None)}
        return {'real': Answer(choice=None, noul=0.1 if 'warning' in state['failure'] else 0.9)}


@pytest.fixture
def jev(monkeypatch):
    fake = _FakeJudge()
    monkeypatch.setattr(judges, 'open_judge', lambda: fake)
    return fake


def test_without_jev_flag_no_judge_is_opened(transcripts, monkeypatch):
    def refuse():
        raise AssertionError('No judge may be opened without --jev')

    monkeypatch.setattr(judges, 'open_judge', refuse)
    transcripts.write([
        transcripts.assistant([{'type': 'text', 'text': 'Say "ship" and I will push.'}]),
        transcripts.user('ship'),
    ])
    cli.Scope(root=str(transcripts.root)).asks()


def test_kinds_of_asks_one_question_per_closing(jev):
    kinds = judge.kinds_of_asks(['Say "ship" and I will push.', 'The commit says Refs #188.'], judge=jev)
    assert kinds == ['permission', 'report']
    state, questions = jev.calls[0]
    assert state == {'closing': 'Say "ship" and I will push.'}
    assert set(questions['kind'].criteria) == {'permission', 'choice', 'clarifying', 'review', 'report'}


def test_real_failures_keeps_only_real_ones(jev):
    failures = ['Edit: C:\\a.py → File has not been read yet.', 'PowerShell: git add . → warning: LF will be replaced']
    assert judge.real_failures(failures, judge=jev) == [True, False]
    assert jev.calls[1][0] == {'failure': 'PowerShell: git add . → warning: LF will be replaced'}


def test_no_judge_fails_the_jev_report(transcripts, monkeypatch):
    monkeypatch.setattr(judges, 'open_judge', lambda: None)
    transcripts.write([
        transcripts.assistant([{'type': 'text', 'text': 'Say "ship" and I will push.'}]),
        transcripts.user('ship'),
    ])
    with pytest.raises(cli.findings.RepeatOScopeError, match='No judge available'):
        cli.Scope(root=str(transcripts.root), jev=True).asks()


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
    assert 'fake judged 1 group(s) as noise' in failures, report
