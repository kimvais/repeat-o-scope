import json
import re
from datetime import timedelta

import pytest

from repeat_o_scope import cli, findings, usage
from repeat_o_scope.usage import Row, Usage

_USAGE = {'input_tokens': 10, 'cache_creation_input_tokens': 100, 'cache_read_input_tokens': 1000, 'output_tokens': 20}


def _call(transcripts, *, id, model='claude-opus-5-5', usage=_USAGE, **extra):
    entry = transcripts.assistant([{'type': 'text', 'text': 'hi'}], **extra)
    entry['message'] |= {'id': id, 'model': model, 'usage': usage}
    return entry


def _count(transcripts):
    return usage.count(days=30, root=transcripts.root, since=transcripts.now - timedelta(days=30))


def _row(model, *, sessions=1, turns=1, weight=10 + 125 + 50 + 100):
    return Row(
        model=model,
        sessions=sessions,
        turns=turns,
        input=10 * turns,
        cache_write=100 * turns,
        cache_read=1000 * turns,
        output=20 * turns,
        weight=weight * turns,
    )


def test_nothing_to_count(transcripts):
    transcripts.root.mkdir(parents=True)
    assert _count(transcripts) == Usage(days=30, models=[], total=_row('Total', sessions=0, turns=0))


def test_entries_without_usage_or_id_are_not_turns(transcripts):
    no_usage = transcripts.assistant([{'type': 'text', 'text': 'hi'}])
    no_usage['message']['id'] = 'm1'
    no_id = transcripts.assistant([{'type': 'text', 'text': 'hi'}])
    no_id['message']['usage'] = _USAGE
    transcripts.write([no_usage, no_id, transcripts.user('ship'), '{"truncated'])
    assert _count(transcripts).total == _row('Total', sessions=0, turns=0)


def test_synthetic_messages_are_not_turns(transcripts):
    transcripts.write([_call(transcripts, id='m1', model='<synthetic>')])
    assert _count(transcripts).total == _row('Total', sessions=0, turns=0)


def test_old_transcripts_are_left_out(transcripts):
    old = _call(transcripts, id='m1')
    old['timestamp'] = transcripts.stamp(days_ago=31)
    transcripts.write([old])
    assert _count(transcripts).total.turns == 0


def test_one_call_split_over_entries_counts_once(transcripts):
    transcripts.write([_call(transcripts, id='m1'), _call(transcripts, id='m1')])
    assert _count(transcripts).total == _row('Total')


def test_rows_per_model_heaviest_first(transcripts):
    transcripts.write([
        _call(transcripts, id='m1', session='s-1'),
        _call(transcripts, id='m2', session='s-2'),
        _call(transcripts, id='m3', session='s-2', model='claude-haiku-5-5'),
        _call(transcripts, id='m4', session='s-3', model=None),
    ])
    # A subagent shares its parent's session id: more turns, not another session.
    transcripts.write([_call(transcripts, id='m5', session='s-1', agentId='a1')], subagent=True)
    haiku = 10 + 125 + 100 + 100
    assert _count(transcripts) == Usage(
        days=30,
        models=[
            _row('claude-opus-5-5', sessions=2, turns=3),
            # A tie keeps the order the models were first seen in.
            _row('claude-haiku-5-5', weight=haiku),
            _row(usage.UNKNOWN_MODEL, weight=haiku),
        ],
        total=Row(
            model='Total',
            sessions=3,
            turns=5,
            input=50,
            cache_write=500,
            cache_read=5000,
            output=100,
            weight=3 * (10 + 125 + 50 + 100) + 2 * haiku,
        ),
    )


def test_unknown_format(transcripts):
    with pytest.raises(findings.RepeatOScopeError, match='xml'):
        cli.Scope(root=str(transcripts.root), format='xml').usage()


def test_usage_markdown(transcripts):
    transcripts.write([_call(transcripts, id='m1')])
    out = cli.Scope(root=str(transcripts.root), format='markdown', price=3.0).usage()
    assert out.splitlines() == [
        '# Usage',
        '',
        '_Last 30 days: 1 sessions, 1 turns, 1,130 tokens._',
        '',
        '| Model | Sessions | Turns | Input | Cache writes | Cache reads | Output | Share of usage | € |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|',
        '| claude-opus-5-5 | 1 | 1 | 10 | 100 | 1,000 | 20 | 100.0% | €0.00 |',
        '| Total | 1 | 1 | 10 | 100 | 1,000 | 20 | 100.0% | €0.00 |',
    ], out


def test_usage_json(transcripts):
    transcripts.write([_call(transcripts, id='m1')])
    doc = json.loads(cli.Scope(root=str(transcripts.root), format='json').usage())
    total = {
        'model': 'Total',
        'sessions': 1,
        'turns': 1,
        'input': 10,
        'cache_write': 100,
        'cache_read': 1000,
        'output': 20,
        'weight': 285.0,
        'tokens': 1130,
        'share': 1.0,
    }
    assert doc | {'generated_at': None} == {
        'report': 'usage',
        'days': 30,
        'generated_at': None,
        'total': total,
        'models': [total | {'model': 'claude-opus-5-5'}],
    }, doc


@pytest.mark.parametrize(('tokens', 'shown'), [
    (0, '0'),
    (999, '999'),
    (1_000, '1.0k'),
    (26_150_000, '26.1M'),
    (2_027_000_000, '2.0B'),
])
def test_usage_table_shortens_tokens(transcripts, tokens, shown):
    transcripts.write([_call(transcripts, id='m1', usage={'cache_read_input_tokens': tokens})])
    out = cli.Scope(root=str(transcripts.root)).usage()
    assert re.search(rf'│ +{re.escape(shown)} │ +0 │', out), out
