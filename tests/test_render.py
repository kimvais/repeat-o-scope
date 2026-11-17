import json
import os
import subprocess
import sys

import pytest

from repeat_o_scope import cli, findings

_SECRET = 'glpat-abcdefghij1234567890'


def _json(transcripts, report, **kwargs):
    return getattr(cli.Scope(root=str(transcripts.root), format='json', **kwargs), report)()


def test_unknown_format_raises(transcripts):
    with pytest.raises(findings.RepeatOScopeError, match='xml'):
        cli.Scope(root=str(transcripts.root), format='xml').prompts()


def test_json_no_secret_anywhere(transcripts):
    entries = []
    for i in range(4):
        entries += [
            transcripts.user(f'deploy with {_SECRET}'),
            transcripts.user(f'<bash-input>curl -H "{_SECRET}"</bash-input>'),
            transcripts.assistant([{'type': 'text', 'text': f'Say ship and I will push {_SECRET}.'}]),
            transcripts.user('ship'),
            transcripts.assistant([{'type': 'tool_use', 'id': f'u{i}', 'name': 'Bash', 'input': {'command': f'echo {_SECRET}'}}]),
            transcripts.user([{'type': 'tool_result', 'tool_use_id': f'u{i}', 'is_error': True, 'content': f'bad {_SECRET}'}]),
        ]
    transcripts.write(entries)
    for report in ('prompts', 'commands', 'asks', 'fails'):
        out = _json(transcripts, report)
        assert json.loads(out)['findings'], (report, out)
        assert _SECRET not in out, (report, out)


def test_json_empty_window(transcripts):
    transcripts.root.mkdir(parents=True)
    doc = json.loads(_json(transcripts, 'prompts'))
    assert (doc['findings'], doc['totals']['items']) == ([], 0)


def test_json_whole_document(transcripts):
    transcripts.write([transcripts.user('ship') for _ in range(5)])
    doc = json.loads(_json(transcripts, 'prompts'))
    assert doc.pop('generated_at').endswith('+00:00')
    assert doc == {
        'report': 'prompts',
        'days': 30,
        'jev': False,
        'totals': {'items': 5, 'groups': 1, 'one_offs': 0},
        'findings': [
            {
                'id': findings.finding_id(report='prompts', top_variant='ship'),
                'kind': 'prompt',
                'total': 5,
                'variants': [{'text': 'ship', 'count': 5}],
                'examples': ['ship'],
                'label': None,
            }
        ],
    }


def test_json_utf8_on_legacy_console(transcripts, tmp_path):
    transcripts.write([transcripts.user('tämä → ✓') for _ in range(4)])
    run = subprocess.run(
        [
            sys.executable, '-c', 'from repeat_o_scope.cli import main; main()',
            'prompts', '--format=json', f'--root={transcripts.root}', f'--data_dir={tmp_path / "data"}',
        ],
        capture_output=True,
        env={**os.environ, 'PYTHONIOENCODING': 'cp1252'},
        timeout=120,
    )
    assert run.returncode == 0, run.stderr.decode('utf-8', 'replace')
    assert 'tämä → ✓' in run.stdout.decode('utf-8')


def _measurement(**waste):
    from repeat_o_scope.waste import KINDS, Measurement, Tally

    zero = Tally(0, 0, 0.0)
    total = waste.pop('total', zero)
    return Measurement(days=30, total=total, waste={k: waste.get(k, zero) for k in KINDS})


def test_measure_unknown_format_raises(transcripts):
    with pytest.raises(findings.RepeatOScopeError, match='xml'):
        cli.Scope(root=str(transcripts.root), format='xml').measure()


def test_measure_with_no_transcripts():
    from repeat_o_scope import render

    m = _measurement()
    assert '0 of 0 turns wasted' in render.measurement_markdown(m, price=None)
    doc = json.loads(render.measurement_json(m, price=None))
    assert {k: v['share'] for k, v in doc['waste'].items()} == dict.fromkeys(
        ['refused_edit', 'reread', 'failed', 'denied', 'bare_yes', 'long_context'], 0
    ), doc


def test_measure_has_no_euros_without_price():
    from repeat_o_scope import render
    from repeat_o_scope.waste import Tally

    m = _measurement(total=Tally(100, 1_000_000, 500_000.0), refused_edit=Tally(10, 50_000, 25_000.0))
    for text in (render.measurement_markdown(m, price=None), render.measurement_json(m, price=None)):
        assert '€' not in text and 'eur' not in text, text


def test_measure_markdown():
    from repeat_o_scope import render
    from repeat_o_scope.waste import Tally

    m = _measurement(total=Tally(100, 1_000_000, 500_000.0), refused_edit=Tally(10, 50_000, 25_000.0))
    text = render.measurement_markdown(m, price=None)
    assert text.startswith('# Wasted turns'), text
    assert '_Last 30 days: 10 of 100 turns wasted, and 0 more ran past half their context: 5.0% of your usage._' in text, text
    assert '| Refused edits | 10 | 5.0% | 50,000 |' in text, text
    assert '| Refused edits | 10 | 5.0% | 50,000 | €0.10 |' in render.measurement_markdown(m, price=4.0)
    assert json.loads(render.measurement_json(m, price=4.0))['waste']['refused_edit']['eur'] == 0.1


def test_measure_headline_counts_long_context_apart():
    from repeat_o_scope import render
    from repeat_o_scope.waste import Tally

    m = _measurement(
        total=Tally(100, 1_000_000, 500_000.0),
        refused_edit=Tally(10, 50_000, 25_000.0),
        long_context=Tally(40, 50_000, 25_000.0),
    )
    text = render.measurement_markdown(m, price=None)
    assert '_Last 30 days: 10 of 100 turns wasted, and 40 more ran past half their context: 10.0% of your usage._' in text, text


def test_measure_default_is_a_rich_table(transcripts):
    text = cli.Scope(root=str(transcripts.root)).measure()
    assert 'Refused edits' in text and '|---' not in text and '# Wasted turns' not in text, text
    assert '─' in text, text


def test_measure_markdown_on_request(transcripts):
    text = cli.Scope(root=str(transcripts.root), format='markdown').measure()
    assert text.startswith('# Wasted turns'), text


@pytest.mark.parametrize('name', ['measurement_table', 'measurement_markdown'])
def test_measure_rows_sorted_by_tokens_descending(name):
    from repeat_o_scope import render
    from repeat_o_scope.waste import Tally

    m = _measurement(
        total=Tally(100, 1_000_000, 500_000.0),
        refused_edit=Tally(10, 50_000, 25_000.0),
        failed=Tally(5, 100_000, 50_000.0),
        long_context=Tally(40, 10_000, 5_000.0),
    )
    text = getattr(render, name)(m, price=None)
    order = ['Failed calls', 'Refused edits', 'Past half context', 'Re-reads']
    assert [text.index(label) for label in order] == sorted(text.index(label) for label in order), text
