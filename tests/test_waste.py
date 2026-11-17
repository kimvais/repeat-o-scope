from datetime import timedelta

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


def test_call_with_two_failures_is_charged_once(transcripts):
    transcripts.write([
        _call(transcripts, id='m1', blocks=[_tool_use('u1'), _tool_use('u2')]),
        _result(transcripts, 'u1', 'Exit code 1\nboom'),
        _result(transcripts, 'u2', 'Exit code 1\nboom'),
    ])
    assert _measure(transcripts).waste['failed'].turns == 1
