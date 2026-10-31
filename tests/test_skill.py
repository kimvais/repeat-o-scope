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
    ['--format json', 'repeat-o-scope decline', 'Leave it', 'uv tool install repeat-o-scope', 'TYPESAFE_API_KEY', '--all'],
)
def test_skill_mentions_cli_surface(needle):
    assert needle in _SKILL
