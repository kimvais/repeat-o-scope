from importlib import resources

import pytest

_SKILL = resources.files('repeat_o_scope').joinpath('skill', 'SKILL.md').read_text(encoding='utf-8')


def _frontmatter():
    _, head, _ = _SKILL.split('---', 2)
    return dict(line.split(': ', 1) for line in head.strip().splitlines())


def test_skill_frontmatter():
    meta = _frontmatter()
    assert meta['name'] == 'repeat-o-scope'
    assert 'Placeholder' not in meta['description']
    assert len(meta['description']) > 40


@pytest.mark.parametrize(
    'needle',
    [
        'repeat-o-scope audit --format json --jev',
        'repeat-o-scope decline',
        'Leave it',
        'uv tool install repeat-o-scope',
        '--all',
        'model: "haiku"',
        'disable-model-invocation: true',
        'skillOverrides',
        'The Bash tool does not exist here.',
    ],
)
def test_skill_mentions_cli_surface(needle):
    assert needle in _SKILL


@pytest.mark.parametrize('report', ['prompts', 'commands', 'asks', 'fails'])
def test_skill_no_longer_runs_the_separate_reports(report):
    assert f'repeat-o-scope {report} --format json' not in _SKILL


def test_skill_asks_haiku_the_judges_questions():
    from repeat_o_scope import judge

    assert judge._REAL.instructions in _SKILL
    assert all(description in _SKILL for description in judge._ASK.criteria.values())
