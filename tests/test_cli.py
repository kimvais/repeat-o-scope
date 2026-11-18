import json
import subprocess
import sys

import pytest

from repeat_o_scope import cli, findings


def test_report_on_stdout_logs_on_stderr(transcripts, tmp_path):
    transcripts.write([transcripts.user('<bash-input>gcloud auth login</bash-input>') for _ in range(4)])
    run = subprocess.run(
        [
            sys.executable, '-c', 'from repeat_o_scope.cli import main; main()',
            'commands', f'--root={transcripts.root}', f'--data_dir={tmp_path / "data"}',
        ],
        capture_output=True,
        encoding='utf-8',
        timeout=120,
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.startswith('# Commands you keep running'), run.stdout
    assert 'Ranked' in run.stderr, run.stderr


def _scope(transcripts):
    return cli.Scope(root=str(transcripts.root))


def test_prompts_report_lists_top_cluster(transcripts):
    transcripts.write([transcripts.user('ship') for _ in range(5)] + [transcripts.user('commit and push')])
    report = _scope(transcripts).prompts()
    assert report.startswith('# Prompts you keep typing'), report
    assert '5 × ship' in report, report


def test_commands_report_lists_shell_commands(transcripts):
    transcripts.write([transcripts.user('<bash-input>gcloud auth login</bash-input>') for _ in range(4)])
    report = _scope(transcripts).commands()
    assert '4 × gcloud auth login' in report, report


def test_asks_report_counts_go_ahead_replies(transcripts):
    entries = []
    for _ in range(3):
        entries += [
            transcripts.assistant([{'type': 'text', 'text': 'Next: say "ship" to push it.'}]),
            transcripts.user('ship'),
        ]
    entries += [
        transcripts.assistant([{'type': 'text', 'text': 'Which approach, A or B?'}]),
        transcripts.user('B, because it keeps the API stable'),
    ]
    transcripts.write(entries)
    report = _scope(transcripts).asks()
    assert '3 of 4 turn endings' in report, report
    assert 'Next: say "ship" to push it.' in report, report
    assert 'Which approach' not in report, report


def test_fails_report_separates_denials(transcripts):
    entries = []
    for i, error in enumerate(['File has not been read yet.'] * 4 + ["The user doesn't want to proceed with this tool use."]):
        entries += [
            transcripts.assistant([{'type': 'tool_use', 'id': f'u{i}', 'name': 'Edit', 'input': {'file_path': f'C:\\{i}.py'}}]),
            transcripts.user([{'type': 'tool_result', 'tool_use_id': f'u{i}', 'is_error': True, 'content': error}]),
        ]
    transcripts.write(entries)
    report = _scope(transcripts).fails()
    failures, denials = report.split('## Denied')
    assert '4 × edit: file has not been read yet.' in failures, report
    assert '1 denials' in denials, report
    assert "doesn't want to proceed" not in failures, report


def test_doctor_cli(tmp_path):
    run = subprocess.run(
        [
            sys.executable, '-c', 'from repeat_o_scope.cli import main; main()',
            'doctor', f'--claude_dir={tmp_path}', '--format=markdown',
        ],
        capture_output=True,
        cwd=tmp_path,
        encoding='utf-8',
        timeout=120,
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.startswith('# Settings doctor'), run.stdout
    assert 'Checked' in run.stderr, run.stderr


@pytest.mark.parametrize('format', [None, 'markdown', 'json'])
def test_doctor_never_prints_secrets(tmp_path, monkeypatch, format):
    (tmp_path / 'settings.json').write_text(
        json.dumps({
            'env': {'TOKEN': 'sekrit-1'},
            'mcpServers': {'x': {'command': 'npx', 'env': {'KEY': 'sekrit-2'}}},
            'statusLine': {'type': 'command', 'command': 'status --token sekrit-3'},
        }),
        encoding='utf-8',
    )
    monkeypatch.chdir(tmp_path)
    out = cli.Scope(claude_dir=str(tmp_path), format=format).doctor()
    assert 'Status line' in out or 'status_line' in out, out
    assert 'sekrit' not in out, out


def test_doctor_unknown_format(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(findings.RepeatOScopeError, match='xml'):
        cli.Scope(claude_dir=str(tmp_path), format='xml').doctor()


def test_measure_cli(transcripts, tmp_path):
    transcripts.write([transcripts.user('ship')])
    run = subprocess.run(
        [
            sys.executable, '-c', 'from repeat_o_scope.cli import main; main()',
            'measure', f'--root={transcripts.root}', f'--data_dir={tmp_path / "data"}',
        ],
        capture_output=True,
        encoding='utf-8',
        timeout=120,
    )
    assert run.returncode == 0, run.stderr
    assert 'Wasted turns' in run.stdout and 'Refused edits' in run.stdout, run.stdout
    assert '|---' not in run.stdout, run.stdout
    assert 'Measured' in run.stderr, run.stderr


def test_usage_cli(transcripts, tmp_path):
    transcripts.write([transcripts.user('ship')])
    run = subprocess.run(
        [
            sys.executable, '-c', 'from repeat_o_scope.cli import main; main()',
            'usage', f'--root={transcripts.root}', f'--data_dir={tmp_path / "data"}',
        ],
        capture_output=True,
        encoding='utf-8',
        timeout=120,
    )
    assert run.returncode == 0, run.stderr
    assert 'Usage' in run.stdout and 'Sessions' in run.stdout, run.stdout
    assert 'Reading transcripts' not in run.stdout + run.stderr, run.stderr
    assert 'Counted' in run.stderr, run.stderr
