"""One pass over the transcripts and settings: what was wasted, on what, what it cost, and what to check."""

import collections
import dataclasses
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from repeat_o_scope import cluster, doctor, findings, judge, judges, transcripts, waste
from repeat_o_scope.findings import Finding, Report, Variant
from repeat_o_scope.waste import Charge, Measurement, Tally

_LOGGER = logging.getLogger(__name__)

_MIN_SIZE = 4
# Wasted calls cost something even once, so two alike already make a group.
_MIN_WASTE_SIZE = 2
_MAX_EXAMPLES = 3


@dataclass(frozen=True)
class Audit:
    measurement: Measurement
    judge: str | None
    findings: list[Finding]
    checks: list[doctor.Check]
    note: str = ''


def run(*, claude_dir: Path, days: int, judge: bool, project_dir: Path, root: Path, since: datetime) -> Audit:
    """Returns the audit: waste findings ranked by share of usage, then prompts and commands, then settings."""
    m = waste.measure(days=days, root=root, since=since)
    wasted = [f for kind in waste.KINDS for f in _waste_findings(m, kind=kind)]
    opened = judges.open_judge() if judge else None
    note = ''
    if opened is not None:
        wasted, note = _judged(wasted, by=opened)
    wasted.sort(key=lambda f: -f.share)
    counted = [
        *clustered('prompts', 'prompt', transcripts.iter_prompts(root=root, since=since), days=days).findings,
        *clustered('commands', 'command', transcripts.iter_commands(root=root, since=since), days=days).findings,
    ]
    checks = doctor.diagnose(claude_dir=claude_dir, project_dir=project_dir)
    _LOGGER.info('Audited `%s` waste findings and `%s` repeats.', len(wasted), len(counted))
    return Audit(
        measurement=m,
        judge=opened.name if opened else None,
        findings=wasted + counted,
        checks=checks,
        note=note,
    )


def clustered(name: str, kind: str, texts: Iterable[str], *, days: int, jev: bool = False) -> Report:
    """Returns a report of the texts that repeat at least `_MIN_SIZE` times, grouped."""
    originals = collections.defaultdict(list)
    for text in texts:
        originals[cluster.normalise(text)].append(text)
    clusters, noise = cluster.rank(repeated(originals), min_size=_MIN_SIZE)
    _LOGGER.info('Ranked `%s` %s into `%s` groups.', sum(map(len, originals.values())), name, len(clusters))
    return Report(
        name=name,
        days=days,
        jev=jev,
        totals={'items': sum(map(len, originals.values())), 'groups': len(clusters), 'one_offs': noise},
        findings=[findings.from_cluster(report=name, kind=kind, cluster=c, originals=originals) for c in clusters],
    )


def repeated(groups: dict[str, list]) -> list[str]:
    """Returns each key once per item behind it, the shape `cluster.rank` counts."""
    return [k for k, items in groups.items() for _ in items]


def _waste_findings(m: Measurement, *, kind: str) -> list[Finding]:
    """Returns one finding per group of alike wasted calls of `kind`, and one for the rest."""
    behind: dict[str, list[Charge]] = collections.defaultdict(list)
    for c in m.charges:
        if c.kind == kind:
            behind[cluster.normalise(c.evidence)].append(c)
    if not behind:
        return []
    clusters, _ = cluster.rank(repeated(behind), min_size=_MIN_WASTE_SIZE)
    grouped = {text for c in clusters for text, _ in c.variants}
    rest = sorted(((t, len(cs)) for t, cs in behind.items() if t not in grouped), key=lambda v: (-v[1], v[0]))
    groups = [(c.variants, c.variants[0][0]) for c in clusters]
    if rest:
        groups.append((rest, '*'))
    return [_finding(kind=kind, variants=variants, top=top, behind=behind, total=m.total) for variants, top in groups]


def _finding(*, behind: dict[str, list[Charge]], kind: str, top: str, total: Tally, variants: list) -> Finding:
    charges = [c for text, _ in variants for c in behind[text]]
    cost = Tally(
        turns=sum(c.tally.turns for c in charges),
        tokens=sum(c.tally.tokens for c in charges),
        weight=sum(c.tally.weight for c in charges),
    )
    return Finding(
        id=findings.finding_id(report='audit', top_variant=f'{kind}:{top}'),
        kind=kind,
        total=len(charges),
        variants=[Variant(text, count) for text, count in variants],
        examples=list(dict.fromkeys(c.evidence for c in behind[variants[0][0]]))[:_MAX_EXAMPLES],
        label=None,
        cost=cost,
        share=cost.weight / total.weight if total.weight else 0.0,
    )


def _judged(found: list[Finding], *, by: judges.Judge) -> tuple[list[Finding], str]:
    """Returns the findings with failed-call noise dropped and go-aheads labelled, and a note on what was dropped."""
    failed = [f for f in found if f.kind == 'failed']
    asks = [f for f in found if f.kind == 'bare_yes']
    real = judge.real_failures([f.examples[0] for f in failed], judge=by) if failed else []
    kinds = judge.kinds_of_asks([f.examples[0] for f in asks], judge=by) if asks else []
    labels = {f.id: 'real' for f, r in zip(failed, real) if r} | {f.id: k for f, k in zip(asks, kinds)}
    noise = {f.id for f, r in zip(failed, real) if not r}
    kept = [dataclasses.replace(f, label=labels.get(f.id)) for f in found if f.id not in noise]
    note = f'{by.name} judged {len(noise)} group(s) of failed calls as noise on stderr and left them out.'
    return kept, note if failed else ''

