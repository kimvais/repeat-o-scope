import pytest

from repeat_o_scope import cluster


@pytest.mark.parametrize(
    ('text', 'expected'),
    [
        ('Address the review comments on !412', 'address the review comments on <ref>'),
        ('look at https://example.com/a?b=1 now', 'look at <url> now'),
        ('open C:\\Users\\k\\p\\x.py and /tmp/y', 'open <path> and <path>'),
        ('commit 14aabc8 then #85', 'commit <hash> then <ref>'),
        ('run   it\n 3 times', 'run it <n> times'),
    ],
)
def test_normalise(text, expected):
    assert cluster.normalise(text) == expected


def test_too_few_texts_give_no_clusters():
    assert cluster.rank(['ship', 'go'], min_size=4) == ([], 2)


def test_similar_prompts_cluster_and_rank_by_total():
    texts = (
        ['ship'] * 6 + ['ship it'] * 2
        + ['commit and push'] * 3 + ['commit, push and open mr'] * 2
        + [f'unique thought number {w}' for w in ('alpha', 'kettle', 'orbit', 'violin', 'meadow')]
    )
    clusters, noise = cluster.rank(texts, min_size=4)
    assert clusters[0].total >= 8
    assert clusters[0].variants[0] == ('ship', 6)
    assert sum(c.total for c in clusters) + noise == len(texts)


def test_frequent_exact_repeat_is_a_cluster_even_when_unique():
    texts = ['<n>'] * 5 + ['completely different sentence about gardens', 'yet another about trains']
    clusters, _ = cluster.rank(texts, min_size=4)
    assert cluster.Cluster(total=5, variants=[('<n>', 5)]) in clusters
