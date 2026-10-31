from datetime import timedelta

import pytest

from repeat_o_scope import transcripts as t


def _since(transcripts):
    return transcripts.now - timedelta(days=30)


def _prompts(transcripts):
    return list(t.iter_prompts(root=transcripts.root, since=_since(transcripts)))


@pytest.mark.parametrize(
    'noise',
    [
        '<command-name>/clear</command-name>',
        '<local-command-stdout></local-command-stdout>',
        '<system-reminder>x</system-reminder>',
        '<task-notification>done</task-notification>',
        '<cross-session-message from="x">hi</cross-session-message>',
        '<bash-stdout>(PowerShell completed with no output)</bash-stdout><bash-stderr></bash-stderr>',
        'This session is being continued from a previous conversation',
        '[Request interrupted by user]',
        'Base directory for this skill: C:\\x',
        '   ',
    ],
)
def test_plumbing_is_not_a_prompt(transcripts, noise):
    transcripts.write([transcripts.user(noise)])
    assert _prompts(transcripts) == []


@pytest.mark.parametrize(
    'extra',
    [{'isMeta': True}, {'isSidechain': True}, {'entrypoint': 'sdk-cli'}],
    ids=['meta', 'sidechain', 'scripted'],
)
def test_entries_not_typed_by_the_user_are_skipped(transcripts, extra):
    transcripts.write([transcripts.user('ship', **extra)])
    assert _prompts(transcripts) == []


def test_subagent_transcripts_hold_no_prompts(transcripts):
    transcripts.write([transcripts.user('ship')], subagent=True)
    assert _prompts(transcripts) == []


def test_prompts_older_than_window_are_skipped(transcripts):
    transcripts.write([transcripts.user('old', days_ago=31), transcripts.user('new')])
    assert _prompts(transcripts) == ['new']


def test_tool_results_are_not_prompts(transcripts):
    transcripts.write([transcripts.user([{'type': 'tool_result', 'tool_use_id': 'x', 'content': 'ok'}])])
    assert _prompts(transcripts) == []


def test_garbage_lines_are_skipped(transcripts):
    transcripts.write(['not json', '42', '["list"]', transcripts.user('real')])
    assert _prompts(transcripts) == ['real']


def test_prompt_secrets_are_redacted(transcripts):
    transcripts.write([transcripts.user([{'type': 'text', 'text': 'use glpat-abcdefghij1234567890 now'}])])
    assert _prompts(transcripts) == ['use [REDACTED] now']


def test_shell_commands_are_listed_apart_from_prompts(transcripts):
    transcripts.write([
        transcripts.user('<bash-input>gcloud auth login</bash-input>'),
        transcripts.user('<bash-input> git push glpat-abcdefghij1234567890</bash-input>'),
        transcripts.user('ship'),
    ])
    commands = list(t.iter_commands(root=transcripts.root, since=_since(transcripts)))
    assert commands == ['gcloud auth login', 'git push [REDACTED]']
    assert _prompts(transcripts) == ['ship']


def test_turn_end_pairs_closing_line_with_reply(transcripts):
    transcripts.write([
        transcripts.user('fix it'),
        transcripts.assistant([{'type': 'text', 'text': 'Fixed.\n\nNext: say "ship" to push it.'}]),
        transcripts.user('<system-reminder>noise</system-reminder>'),
        transcripts.user('ship'),
        transcripts.assistant([{'type': 'tool_use', 'id': 'u1', 'name': 'Bash', 'input': {}}]),
        transcripts.user('<bash-input>ls</bash-input>'),
    ])
    ends = list(t.iter_turn_ends(root=transcripts.root, since=_since(transcripts)))
    assert ends == [t.TurnEnd(closing='Next: say "ship" to push it.', reply='ship')]


def test_turn_ends_stay_within_their_session(transcripts):
    transcripts.write([transcripts.assistant([{'type': 'text', 'text': 'Done?'}], session='a')], name='a')
    transcripts.write([transcripts.user('yes', session='b')], name='b')
    assert list(t.iter_turn_ends(root=transcripts.root, since=_since(transcripts))) == []


def test_tool_failure_pairs_error_with_attempt(transcripts):
    transcripts.write([
        transcripts.assistant([
            {'type': 'tool_use', 'id': 'u1', 'name': 'Edit', 'input': {'file_path': 'C:\\a.py'}},
            {'type': 'tool_use', 'id': 'u2', 'name': 'Read', 'input': {'file_path': 'C:\\b.py'}},
        ]),
        transcripts.user([
            {
                'type': 'tool_result',
                'tool_use_id': 'u1',
                'is_error': True,
                'content': '<tool_use_error>File has not been read yet.</tool_use_error>',
            },
            {'type': 'tool_result', 'tool_use_id': 'u2', 'content': 'fine'},
        ]),
    ], subagent=True)
    failures = list(t.iter_tool_failures(root=transcripts.root, since=_since(transcripts)))
    assert failures == [t.ToolFailure(tool='Edit', attempt='C:\\a.py', error='File has not been read yet.')]


def test_tool_failure_skips_exit_code_line_and_redacts(transcripts):
    transcripts.write([
        transcripts.assistant([
            {'type': 'tool_use', 'id': 'u1', 'name': 'PowerShell', 'input': {'command': 'echo ghp_' + 'a' * 30}},
        ]),
        transcripts.user([
            {
                'type': 'tool_result',
                'tool_use_id': 'u1',
                'is_error': True,
                'content': [{'type': 'text', 'text': 'Exit code 1\n\nboom ghp_' + 'a' * 30}],
            },
        ]),
    ])
    [failure] = t.iter_tool_failures(root=transcripts.root, since=_since(transcripts))
    assert failure == t.ToolFailure(tool='PowerShell', attempt='echo [REDACTED]', error='boom [REDACTED]')
