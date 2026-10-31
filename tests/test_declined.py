import json

import pytest

from repeat_o_scope import cli, declined, findings
from repeat_o_scope.findings import Finding, Variant


def _scope(transcripts, data_dir, **kwargs):
    return cli.Scope(root=str(transcripts.root), data_dir=str(data_dir), format='json', **kwargs)


def _texts(out):
    return [f['variants'][0]['text'] for f in json.loads(out)['findings']]


def test_decline_unknown_id_raises_and_stores_nothing(tmp_path):
    with pytest.raises(findings.RepeatOScopeError, match='no-such-id'):
        declined.decline(data_dir=tmp_path, finding_id='no-such-id')
    assert not (tmp_path / 'declined.json').exists()


def test_declined_finding_is_left_out_next_run(transcripts, tmp_path):
    transcripts.write([transcripts.user('ship') for _ in range(5)])
    first = json.loads(_scope(transcripts, tmp_path).prompts())['findings'][0]
    message = _scope(transcripts, tmp_path).decline(first['id'])
    assert message == f'Declined `{first["id"]}`: ship'
    assert _texts(_scope(transcripts, tmp_path).prompts()) == []


def test_all_shows_declined_again(transcripts, tmp_path):
    transcripts.write([transcripts.user('ship') for _ in range(5)])
    first = json.loads(_scope(transcripts, tmp_path).prompts())['findings'][0]
    _scope(transcripts, tmp_path).decline(first['id'])
    assert _texts(_scope(transcripts, tmp_path, all=True).prompts()) == ['ship']


def test_declined_survives_top_variant_shift(tmp_path):
    def finding(*variants):
        return Finding(
            id=findings.finding_id(report='prompts', top_variant=variants[0][0]),
            kind='prompt',
            total=sum(n for _, n in variants),
            variants=[Variant(t, n) for t, n in variants],
            examples=[],
            label=None,
        )

    before = finding(('ship', 5), ('ship it', 4))
    declined.remember_last(
        data_dir=tmp_path,
        report=findings.Report(name='prompts', days=30, jev=False, totals={}, findings=[before]),
    )
    declined.decline(data_dir=tmp_path, finding_id=before.id)
    after = finding(('ship it', 6), ('ship', 5))
    other = finding(('go', 7),)
    assert declined.drop_declined(data_dir=tmp_path, findings=[after, other]) == [other]
