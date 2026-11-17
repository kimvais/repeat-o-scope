from datetime import timedelta

import pytest

from repeat_o_scope import waste
from repeat_o_scope.waste import Tally

_USAGE = {'input_tokens': 10, 'cache_creation_input_tokens': 100, 'cache_read_input_tokens': 1000, 'output_tokens': 20}
_TOKENS = 1130
_WEIGHT = 10 + 125 + 100 + 100


def _call(transcripts, *, id, blocks, usage=_USAGE, **extra):
    entry = transcripts.assistant(blocks, **extra)
    entry['message'] |= {'id': id, 'usage': usage}
    return entry


def _tool_use(id, name='Edit', **tool_input):
    return {'type': 'tool_use', 'id': id, 'name': name, 'input': tool_input or {'file_path': 'C:\\a.py'}}


def _result(transcripts, id, error, **extra):
    return transcripts.user([{'type': 'tool_result', 'tool_use_id': id, 'is_error': True, 'content': error}], **extra)


def _measure(transcripts):
    return waste.measure(days=30, root=transcripts.root, since=transcripts.now - timedelta(days=30))


def _zero(m, *, but=()):
    return {k: t for k, t in m.waste.items() if k not in but and t != Tally(0, 0, 0.0)}


def test_entries_without_usage_are_not_turns(transcripts):
    no_usage = transcripts.assistant([_tool_use('u1')])
    no_usage['message']['id'] = 'm1'
    no_id = transcripts.assistant([{'type': 'text', 'text': 'hi'}])
    no_id['message']['usage'] = _USAGE
    transcripts.write([no_usage, _result(transcripts, 'u1', 'Exit code 1\nboom'), no_id, '{"truncated'])
    m = _measure(transcripts)
    assert m.total == Tally(0, 0, 0.0), m
    assert _zero(m) == {}, m
    assert list(m.waste) == list(waste.KINDS), m


def test_one_call_split_over_entries_counts_once(transcripts):
    transcripts.write([
        _call(transcripts, id='m1', blocks=[{'type': 'thinking', 'thinking': '...'}]),
        _call(transcripts, id='m1', blocks=[_tool_use('u1', name='Read')]),
    ])
    assert _measure(transcripts).total == Tally(turns=1, tokens=_TOKENS, weight=_WEIGHT)


@pytest.mark.parametrize(('model', 'read_ratio'), [
    (None, 0.1),
    ('<synthetic>', 0.1),
    ('claude-opus-4-8', 0.1),
    ('claude-fable-5', 0.1),
    ('claude-opus-5-5', 0.05),
    ('claude-sonnet-5-5', 0.05),
    ('claude-fable-5-2', 0.025),
    ('claude-mythos-5-1', 0.025),
])
def test_cache_reads_weigh_by_model(transcripts, model, read_ratio):
    entry = _call(transcripts, id='m1', blocks=[], usage={'cache_read_input_tokens': 1000})
    entry['message']['model'] = model
    transcripts.write([entry])
    assert _measure(transcripts).total == Tally(turns=1, tokens=1000, weight=1000 * read_ratio)


@pytest.mark.parametrize(('split', 'weight'), [
    (None, 375.0),
    ({'ephemeral_5m_input_tokens': 300, 'ephemeral_1h_input_tokens': 0}, 375.0),
    ({'ephemeral_5m_input_tokens': 100, 'ephemeral_1h_input_tokens': 200}, 525.0),
    ({'ephemeral_5m_input_tokens': 0, 'ephemeral_1h_input_tokens': 999}, 600.0),
])
def test_one_hour_cache_writes_weigh_double(transcripts, split, weight):
    usage = {'cache_creation_input_tokens': 300} | ({'cache_creation': split} if split else {})
    transcripts.write([_call(transcripts, id='m1', blocks=[], usage=usage)])
    assert _measure(transcripts).total == Tally(turns=1, tokens=300, weight=weight)


def test_tool_errors_are_charged_to_one_kind(transcripts):
    errors = [
        '<tool_use_error>File has not been read yet. Read it first before writing to it.</tool_use_error>',
        'Permission for this action was denied by the Claude Code auto mode classifier.',
        'Exit code 1\nNo such file',
    ]
    entries = []
    for i, error in enumerate(errors):
        entries += [_call(transcripts, id=f'm{i}', blocks=[_tool_use(f'u{i}')]), _result(transcripts, f'u{i}', error)]
    transcripts.write(entries)
    m = _measure(transcripts)
    one = Tally(turns=1, tokens=_TOKENS, weight=_WEIGHT)
    assert {k: m.waste[k] for k in ('refused_edit', 'denied', 'failed')} == {
        'refused_edit': one, 'denied': one, 'failed': one,
    }, m
    assert _zero(m, but=('refused_edit', 'denied', 'failed')) == {}, m


def _read(transcripts, id, **tool_input):
    return _call(transcripts, id=id, blocks=[_tool_use(f'u-{id}', name='Read', **tool_input)])


def test_second_read_is_a_reread(transcripts):
    transcripts.write([_read(transcripts, 'm1'), _read(transcripts, 'm2')])
    assert _measure(transcripts).waste['reread'] == Tally(turns=1, tokens=_TOKENS, weight=_WEIGHT)


def test_read_after_edit_is_not_a_reread(transcripts):
    edit = _call(transcripts, id='m2', blocks=[_tool_use('u-e', name='Edit', file_path='C:\\a.py')])
    transcripts.write([_read(transcripts, 'm1'), edit, _read(transcripts, 'm3')])
    assert _measure(transcripts).waste['reread'].turns == 0


def test_read_after_compaction_is_not_a_reread(transcripts):
    summary = transcripts.user(
        'This session is being continued from a previous conversation that ran out of context.',
        isCompactSummary=True,
    )
    transcripts.write([_read(transcripts, 'm1'), summary, _read(transcripts, 'm2')])
    assert _measure(transcripts).waste['reread'].turns == 0


def test_reads_of_different_ranges_are_not_rereads(transcripts):
    transcripts.write([
        _read(transcripts, 'm1', file_path='C:\\a.py', offset=1, limit=100),
        _read(transcripts, 'm2', file_path='C:\\a.py', offset=101, limit=100),
    ])
    assert _measure(transcripts).waste['reread'].turns == 0


def test_subagent_read_is_its_own_context(transcripts):
    transcripts.write([_read(transcripts, 'm1')])
    sub = _call(transcripts, id='m2', blocks=[_tool_use('u-s', name='Read')], agentId='a1', isSidechain=True)
    transcripts.write([sub], subagent=True)
    assert _measure(transcripts).waste['reread'].turns == 0


_NEXT_USAGE = {'input_tokens': 1, 'cache_creation_input_tokens': 0, 'cache_read_input_tokens': 0, 'output_tokens': 1}


def _ask_and_reply(transcripts, reply, *, session='s-1', **extra):
    return [
        _call(transcripts, id=f'{session}-1', blocks=[{'type': 'text', 'text': 'Shall I commit?'}], session=session),
        transcripts.user(reply, session=session, **extra),
        _call(transcripts, id=f'{session}-2', blocks=[{'type': 'text', 'text': 'Done.'}], usage=_NEXT_USAGE, session=session),
    ]


def test_go_ahead_charges_the_next_call(transcripts):
    transcripts.write(_ask_and_reply(transcripts, 'yes'))
    assert _measure(transcripts).waste['bare_yes'] == Tally(turns=1, tokens=2, weight=6.0)


def test_real_answer_is_not_bare_yes(transcripts):
    transcripts.write(_ask_and_reply(transcripts, 'B, because it keeps the API stable'))
    assert _measure(transcripts).waste['bare_yes'].turns == 0


def test_go_ahead_in_subagent_or_sdk_run_is_not_counted(transcripts):
    transcripts.write(_ask_and_reply(transcripts, 'yes', isSidechain=True), name='side')
    transcripts.write(_ask_and_reply(transcripts, 'yes', entrypoint='sdk-cli', session='s-2'), name='sdk')
    assert _measure(transcripts).waste['bare_yes'].turns == 0


def _sized(transcripts, id, context, blocks=()):
    return _call(transcripts, id=id, blocks=list(blocks), usage={'cache_read_input_tokens': context})


def test_call_past_half_of_200k_is_long_context(transcripts):
    transcripts.write([_sized(transcripts, 'm1', 90_000), _sized(transcripts, 'm2', 130_000)])
    weight = 13_000 * 30_000 / 130_000
    assert _measure(transcripts).waste['long_context'] == Tally(turns=1, tokens=30_000, weight=weight)


def test_session_past_200k_uses_1m_window(transcripts):
    transcripts.write([_sized(transcripts, 'm1', 130_000), _sized(transcripts, 'm2', 600_000)])
    assert _measure(transcripts).waste['long_context'] == Tally(turns=1, tokens=100_000, weight=10_000.0)


def test_call_charged_elsewhere_is_not_long_context(transcripts):
    transcripts.write([
        _sized(transcripts, 'm1', 130_000, blocks=[_tool_use('u1')]),
        _result(transcripts, 'u1', 'File has not been read yet. Read it first before writing to it.'),
    ])
    m = _measure(transcripts)
    assert (m.waste['refused_edit'].turns, m.waste['long_context'].turns) == (1, 0), m


def _timed(transcripts, id, *, minutes_ago, usage):
    stamp = (transcripts.now - timedelta(minutes=minutes_ago)).isoformat()
    entry = _call(transcripts, id=id, blocks=[], usage=usage, timestamp=stamp)
    entry['message']['model'] = 'claude-opus-5-5'
    return entry


def _written(tokens, ttl='5m'):
    return {'cache_creation_input_tokens': tokens, 'cache_creation': {f'ephemeral_{ttl}_input_tokens': tokens}}


_WARM = {'cache_read_input_tokens': 90_000}


def test_write_within_ttl_is_not_a_rebuild(transcripts):
    transcripts.write([
        _timed(transcripts, 'm1', minutes_ago=14, usage=_WARM),
        _timed(transcripts, 'm2', minutes_ago=10, usage=_written(95_000)),
    ])
    assert _measure(transcripts).waste['cache_rebuild'] == Tally(0, 0, 0.0)


def test_gap_without_cache_write_is_not_a_rebuild(transcripts):
    transcripts.write([
        _timed(transcripts, 'm1', minutes_ago=40, usage=_WARM),
        _timed(transcripts, 'm2', minutes_ago=10, usage={'cache_read_input_tokens': 95_000}),
    ])
    assert _measure(transcripts).waste['cache_rebuild'] == Tally(0, 0, 0.0)


@pytest.mark.parametrize(('ttl', 'gap', 'rebuilt', 'rate'), [
    ('5m', 10, 1, 1.25 - 0.05),
    ('1h', 10, 0, 0),
    ('1h', 70, 1, 2.0 - 0.05),
])
def test_write_after_ttl_rebuilds_the_previous_context(transcripts, ttl, gap, rebuilt, rate):
    transcripts.write([
        _timed(transcripts, 'm1', minutes_ago=gap + 1, usage=_WARM),
        _timed(transcripts, 'm2', minutes_ago=1, usage=_written(95_000, ttl)),
    ])
    t = _measure(transcripts).waste['cache_rebuild']
    assert (t.turns, t.tokens) == (rebuilt, 90_000 * rebuilt), t
    assert t.weight == pytest.approx(90_000 * rate), t


def test_rebuild_is_not_long_context(transcripts):
    transcripts.write([
        _timed(transcripts, 'm1', minutes_ago=20, usage=_WARM),
        _timed(transcripts, 'm2', minutes_ago=1, usage=_written(130_000)),
    ])
    m = _measure(transcripts)
    assert (m.waste['cache_rebuild'].turns, m.waste['long_context'].turns) == (1, 0), m


def test_rebuild_charged_elsewhere_counts_once(transcripts):
    rebuild = _timed(transcripts, 'm2', minutes_ago=1, usage=_written(95_000))
    rebuild['message']['content'] = [_tool_use('u1')]
    transcripts.write([
        _timed(transcripts, 'm1', minutes_ago=20, usage=_WARM),
        rebuild,
        _result(transcripts, 'u1', 'Exit code 1\nboom'),
    ])
    m = _measure(transcripts)
    assert (m.waste['failed'].turns, m.waste['cache_rebuild'].turns) == (1, 0), m


@pytest.mark.parametrize('error', [
    '<tool_use_error>No such tool available: Bash</tool_use_error>',
    "Permission to use Grep has been denied because Claude Code is running in don't ask mode.",
])
def test_unavailable_or_denied_tool_is_denied(transcripts, error):
    transcripts.write([_call(transcripts, id='m1', blocks=[_tool_use('u1', name='Bash')]), _result(transcripts, 'u1', error)])
    m = _measure(transcripts)
    assert (m.waste['denied'].turns, m.waste['failed'].turns) == (1, 0), m


def test_cowork_audit_sessions_are_separate_contexts(transcripts):
    """Cowork's audit.jsonl names the session `session_id`, not `sessionId`."""
    entries = []
    for session in ('a', 'b'):
        entry = _read(transcripts, f'm-{session}')
        del entry['sessionId']
        entries.append(entry | {'session_id': session})
    transcripts.write(entries, name='audit')
    assert _measure(transcripts).waste['reread'].turns == 0


def test_charges_carry_evidence(transcripts):
    transcripts.write([
        _call(transcripts, id='m1', blocks=[_tool_use('u1', name='Edit', file_path='C:\\a.py')]),
        _result(transcripts, 'u1', 'Exit code 1\nboom'),
        _read(transcripts, 'm2'),
        _read(transcripts, 'm3'),
    ])
    one = Tally(turns=1, tokens=_TOKENS, weight=_WEIGHT)
    assert _measure(transcripts).charges == [
        waste.Charge(kind='failed', evidence='Edit: C:\\a.py → boom', tally=one),
        waste.Charge(kind='reread', evidence='C:\\a.py', tally=one),
    ]


def test_go_ahead_charge_quotes_the_closing_line(transcripts):
    transcripts.write(_ask_and_reply(transcripts, 'yes'))
    assert [(c.kind, c.evidence) for c in _measure(transcripts).charges] == [('bare_yes', 'Shall I commit?')]


def test_empty_error_is_still_charged(transcripts):
    transcripts.write([_call(transcripts, id='m1', blocks=[_tool_use('u1')]), _result(transcripts, 'u1', '')])
    assert [(c.kind, c.evidence) for c in _measure(transcripts).charges] == [
        ('failed', f'Edit: C:\\a.py → {waste.NO_EVIDENCE}'),
    ]


def test_evidence_is_redacted(transcripts):
    command = 'curl -H "Authorization: Bearer sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789"'
    transcripts.write([
        _call(transcripts, id='m1', blocks=[_tool_use('u1', name='PowerShell', command=command)]),
        _result(transcripts, 'u1', 'Exit code 1\nboom'),
    ])
    assert 'sk-ant-api03' not in _measure(transcripts).charges[0].evidence


def test_call_with_two_failures_is_charged_once(transcripts):
    transcripts.write([
        _call(transcripts, id='m1', blocks=[_tool_use('u1'), _tool_use('u2')]),
        _result(transcripts, 'u1', 'Exit code 1\nboom'),
        _result(transcripts, 'u2', 'Exit code 1\nboom'),
    ])
    assert _measure(transcripts).waste['failed'].turns == 1


def test_notebook_reread_does_not_crash(transcripts):
    transcripts.write([_read(transcripts, f'm{i}', notebook_path='C:\\n.ipynb') for i in (1, 2)])
    assert [(c.kind, c.evidence) for c in _measure(transcripts).charges] == [('reread', 'C:\\n.ipynb')]
