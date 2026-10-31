"""What a report finds: ranked findings a person or a skill can act on."""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field

from repeat_o_scope.cluster import Cluster

# Fixed so that a finding keeps its id across runs and machines.
_NAMESPACE = uuid.UUID('6f2c5b1e-8d4a-4c3e-9a7b-2e1f0d9c8b7a')
_MAX_EXAMPLES = 3


class RepeatOScopeError(ValueError):
    """Raised on input repeat-o-scope cannot act on."""


@dataclass(frozen=True)
class Variant:
    text: str
    count: int


@dataclass(frozen=True)
class Finding:
    id: str
    kind: str  # prompt | command | ask | failure | denial
    total: int
    variants: list[Variant]
    examples: list[str]
    label: str | None


@dataclass(frozen=True)
class Report:
    name: str
    days: int
    jev: bool
    totals: dict[str, int]
    findings: list[Finding]
    note: str = field(default='')


def finding_id(*, report: str, top_variant: str) -> str:
    """Returns a stable id for the finding whose most frequent variant is `top_variant`."""
    return str(uuid.uuid5(_NAMESPACE, f'{report}:{top_variant}'))


def from_cluster(
    *,
    cluster: Cluster,
    kind: str,
    label: str | None = None,
    originals: Mapping[str, list[str]],
    report: str,
) -> Finding:
    """Returns the finding for `cluster`, with examples from the originals behind its top variant."""
    top = cluster.variants[0][0]
    return Finding(
        id=finding_id(report=report, top_variant=top),
        kind=kind,
        total=cluster.total,
        variants=[Variant(text, count) for text, count in cluster.variants],
        examples=list(dict.fromkeys(originals.get(top, [])))[:_MAX_EXAMPLES],
        label=label,
    )
