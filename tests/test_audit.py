import dataclasses
import json
from datetime import timedelta
from pathlib import Path

import pytest

from repeat_o_scope import audit, cli, doctor, judges
from repeat_o_scope.judges import Answer
from repeat_o_scope.waste import Tally

_SMALL = {'input_tokens': 100}
_BIG = {'input_tokens': 10_000}


def _failed(transcripts, i, *, error='Exit code 1\nboom', usage=_SMALL, path='C:\\a.py'):
    call = transcripts.assistant([{'type': 'tool_use', 'id': f'u{i}', 'name': 'Edit', 'input': {'file_path': path}}])
    call['message'] |= {'id': f'm{i}', 'usage': usage}
    result = transcripts.user([{'type': 'tool_result', 'tool_use_id': f'u{i}', 'is_error': True, 'content': error}])
    return [call, result]


def _read(transcripts, i, *, usage=_SMALL):
    call = transcripts.assistant([{'type': 'tool_use', 'id': f'r{i}', 'name': 'Read', 'input': {'file_path': 'C:\\b.py'}}])
    call['message'] |= {'id': f'r{i}', 'usage': usage}
    return call


def _run(transcripts, tmp_path, *, judge=True):
    return audit.run(
        claude_dir=tmp_path / 'claude',
        days=30,
        judge=judge,
        project_dir=tmp_path / 'project',
        root=transcripts.root,
        since=transcripts.now - timedelta(days=30),
    )


@pytest.fixture
def no_judge(monkeypatch):
    monkeypatch.setattr(judges, 'open_judge', lambda: None)


def _waste(a):
    return [f for f in a.findings if f.cost is not None]


def test_no_judge_leaves_groups_unjudged(transcripts, tmp_path, no_judge):
    transcripts.write(_failed(transcripts, 1) + _failed(transcripts, 2) + [_read(transcripts, 1), _read(transcripts, 2)])
    a = _run(transcripts, tmp_path)
    assert a.judge is None
    assert [(f.kind, f.total, f.label, f.cost) for f in _waste(a)] == [
        ('failed', 2, None, Tally(turns=2, tokens=200, weight=200.0)),
        ('reread', 1, None, Tally(turns=1, tokens=100, weight=100.0)),
    ]


def test_findings_rank_by_share_not_count(transcripts, tmp_path, no_judge):
    reads = [_read(transcripts, i) for i in range(4)]
    transcripts.write(reads + _failed(transcripts, 1, usage=_BIG) + _failed(transcripts, 2, usage=_BIG))
    assert [(f.kind, f.total) for f in _waste(_run(transcripts, tmp_path))] == [('failed', 2), ('reread', 3)]


def test_shares_add_up_to_the_measured_waste(transcripts, tmp_path, no_judge):
    transcripts.write(_failed(transcripts, 1) + _failed(transcripts, 2, error='Exit code 1\nother') + [_read(transcripts, 1), _read(transcripts, 2), _read(transcripts, 3)])
    a = _run(transcripts, tmp_path)
    wasted = sum(c.tally.weight for c in a.measurement.charges)
    assert sum(f.share for f in _waste(a)) == pytest.approx(wasted / a.measurement.total.weight)


class _Judge(judges.Judge):
    name = 'fake'
    env = ()

    async def _ask(self, *, questions, state):
        if 'closing' in state:
            return {'kind': Answer(choice='permission', noul=None)}
        return {'real': Answer(choice=None, noul=0.1 if 'warning' in state['failure'] else 0.9)}


def test_judge_drops_noise_and_labels_asks(transcripts, tmp_path, monkeypatch):
    monkeypatch.setattr(judges, 'open_judge', _Judge)
    ask = transcripts.assistant([{'type': 'text', 'text': 'Shall I commit?'}])
    ask['message'] |= {'id': 'a1', 'usage': _SMALL}
    after = transcripts.assistant([{'type': 'text', 'text': 'Done.'}])
    after['message'] |= {'id': 'a2', 'usage': _SMALL}
    transcripts.write(
        _failed(transcripts, 1) + _failed(transcripts, 2)
        + _failed(transcripts, 3, error='warning: LF') + _failed(transcripts, 4, error='warning: LF')
        + [ask, transcripts.user('yes'), after]
    )
    a = _run(transcripts, tmp_path)
    assert a.judge == 'fake'
    assert [(f.kind, f.total, f.label) for f in _waste(a)] == [('failed', 2, 'real'), ('bare_yes', 1, 'permission')]
    assert a.note == 'fake judged 1 group(s) of failed calls as noise on stderr and left them out.'


def test_without_judge_flag_no_judge_is_opened(transcripts, tmp_path, monkeypatch):
    monkeypatch.setattr(judges, 'open_judge', lambda: pytest.fail('judge opened'))
    transcripts.write(_failed(transcripts, 1))
    assert _run(transcripts, tmp_path, judge=False).judge is None


def test_settings_checks_come_along(transcripts, tmp_path, no_judge):
    assert {c.name for c in _run(transcripts, tmp_path).checks} == set(doctor.CHECKS)


def test_prompts_and_commands_carry_no_cost(transcripts, tmp_path, no_judge):
    transcripts.write([transcripts.user('run the tests please') for _ in range(4)] + [transcripts.user('<bash-input>git status</bash-input>') for _ in range(4)])
    counted = [(f.kind, f.total, f.cost) for f in _run(transcripts, tmp_path).findings if f.cost is None]
    assert counted == [('prompt', 4, None), ('command', 4, None)]


def _cli(transcripts, tmp_path, **kwargs):
    return cli.Scope(root=str(transcripts.root), claude_dir=str(tmp_path / 'claude'), data_dir=str(tmp_path / 'data'), **kwargs)


def test_audit_json_is_the_whole_document(transcripts, tmp_path, no_judge):
    transcripts.write(_failed(transcripts, 1) + _failed(transcripts, 2))
    doc = json.loads(_cli(transcripts, tmp_path, jev=True).audit())
    del doc['generated_at']
    checks = doc.pop('checks')
    finding_id = doc['findings'][0]['id']
    zero = {'turns': 0, 'tokens': 0, 'weight': 0.0, 'share': 0.0}
    assert doc == {
        'report': 'audit',
        'days': 30,
        'judge': None,
        'note': '',
        'total': {'turns': 2, 'tokens': 200, 'weight': 200.0},
        'waste': {k: zero for k in ('refused_edit', 'reread', 'denied', 'bare_yes', 'cache_rebuild', 'long_context')}
        | {'failed': {'turns': 2, 'tokens': 200, 'weight': 200.0, 'share': 1.0}},
        'findings': [{
            'id': finding_id,
            'kind': 'failed',
            'total': 2,
            'variants': [{'text': 'edit: <path> → boom', 'count': 2}],
            'examples': ['Edit: C:\\a.py → boom'],
            'label': None,
            'cost': {'turns': 2, 'tokens': 200, 'weight': 200.0},
            'share': 1.0,
        }],
    }
    expected = doctor.diagnose(claude_dir=tmp_path / 'claude', project_dir=Path.cwd())
    assert checks == [dataclasses.asdict(c) for c in expected]


def test_audit_drops_declined_and_all_brings_them_back(transcripts, tmp_path, no_judge):
    transcripts.write(_failed(transcripts, 1) + _failed(transcripts, 2))
    finding_id = json.loads(_cli(transcripts, tmp_path, format='json').audit())['findings'][0]['id']
    _cli(transcripts, tmp_path).decline(finding_id)
    assert json.loads(_cli(transcripts, tmp_path, format='json').audit())['findings'] == []
    assert len(json.loads(_cli(transcripts, tmp_path, format='json', all=True).audit())['findings']) == 1


def test_audit_writes_the_baseline(transcripts, tmp_path, no_judge):
    transcripts.write(_failed(transcripts, 1))
    _cli(transcripts, tmp_path).audit()
    [baseline] = (tmp_path / 'data' / 'baseline').glob('*.json')
    assert baseline.name == f'{transcripts.now.date().isoformat()}.json'
    assert json.loads(baseline.read_text(encoding='utf-8'))['total'] == {'turns': 1, 'tokens': 100, 'weight': 100.0}


def test_audit_markdown_leads_with_the_headline(transcripts, tmp_path, no_judge):
    transcripts.write(_failed(transcripts, 1) + _failed(transcripts, 2))
    text = _cli(transcripts, tmp_path, format='markdown').audit()
    assert text.splitlines()[:3] == [
        '# Audit',
        '',
        '_Last 30 days: 2 of 2 turns wasted, 0 more rebuilt an expired cache and 0 ran past half their context: 100.0% of your usage._',
    ]
    assert '1. **100.0%** failed, 2 turns: edit: <path> → boom' in text, text


def test_rest_group_is_never_judged_away(transcripts, tmp_path, monkeypatch):
    monkeypatch.setattr(judges, 'open_judge', _Judge)
    transcripts.write(_failed(transcripts, 1, error='Exit code 1\nwarning: one') + _failed(transcripts, 2, error='Exit code 1\nzz real two', path='C:\\z.py'))
    a = _run(transcripts, tmp_path)
    assert [(f.kind, f.total) for f in _waste(a)] == [('failed', 2)]


def test_rereads_group_per_file(transcripts, tmp_path, no_judge):
    def read(i, path):
        call = transcripts.assistant([{'type': 'tool_use', 'id': f'r{i}', 'name': 'Read', 'input': {'file_path': path}}])
        call['message'] |= {'id': f'r{i}', 'usage': _SMALL}
        return call

    transcripts.write([read(i, 'C:\\a.py') for i in range(3)] + [read(i, 'C:\\b.py') for i in range(3, 6)])
    assert sorted(f.variants[0].text for f in _waste(_run(transcripts, tmp_path))) == ['C:\\a.py', 'C:\\b.py']
