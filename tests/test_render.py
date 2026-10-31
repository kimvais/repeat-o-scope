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
