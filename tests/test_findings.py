from repeat_o_scope import cli, findings
from repeat_o_scope.cluster import Cluster
from repeat_o_scope.findings import Variant


def test_finding_id_is_stable_and_report_scoped():
    a = findings.finding_id(report='prompts', top_variant='ship')
    assert a == findings.finding_id(report='prompts', top_variant='ship')
    assert a != findings.finding_id(report='commands', top_variant='ship')
    assert a != findings.finding_id(report='prompts', top_variant='go')


def test_from_cluster_takes_examples_from_top_variant():
    finding = findings.from_cluster(
        report='prompts',
        kind='prompt',
        cluster=Cluster(total=5, variants=[('ship', 4), ('ship it', 1)]),
        originals={'ship': ['Ship', 'ship', 'ship'], 'ship it': ['Ship it']},
    )
    assert finding == findings.Finding(
        id=findings.finding_id(report='prompts', top_variant='ship'),
        kind='prompt',
        total=5,
        variants=[Variant('ship', 4), Variant('ship it', 1)],
        examples=['Ship', 'ship'],
        label=None,
    )


def test_fails_report_puts_denials_in_same_list(transcripts):
    entries = []
    for i, error in enumerate(['File has not been read yet.'] * 4 + ["The user doesn't want to proceed with this tool use."] * 4):
        entries += [
            transcripts.assistant([{'type': 'tool_use', 'id': f'u{i}', 'name': 'Edit', 'input': {'file_path': f'C:\\{i}.py'}}]),
            transcripts.user([{'type': 'tool_result', 'tool_use_id': f'u{i}', 'is_error': True, 'content': error}]),
        ]
    transcripts.write(entries)
    report = cli.Scope(root=str(transcripts.root))._report_fails()
    assert {f.kind for f in report.findings} == {'failure', 'denial'}
    assert report.totals['denials'] == 4
    failure = next(f for f in report.findings if f.kind == 'failure')
    assert failure.examples[0] == 'Edit: C:\\0.py → File has not been read yet.'
