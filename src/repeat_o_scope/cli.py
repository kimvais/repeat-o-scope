"""repeat-o-scope: reports on the last N days of Claude Code transcripts."""

import collections
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from repeat_o_scope import cluster, findings, judge, render, transcripts
from repeat_o_scope.findings import Finding, Report, Variant

_LOGGER = logging.getLogger(__name__)

# A reply that only says "go on": the turn before it asked for nothing that needed asking.
_GO_AHEAD = re.compile(
    r'^(ship|ship it|go|go ahead|go for it|yes|yes please|yes, please|y|ok|okay|do it|do that|push|push it'
    r'|commit|commit it|commit and push|commit, push and open mr|post|post it|post them|continue|proceed|sure'
    r'|yep|please do)[.!]*$'
)
# Tool calls refused by a person, a hook or the permission classifier: not failures to automate away.
_DENIED = re.compile(
    r"permission for this action was denied|doesn't want to proceed|denied this in vjuu|hook error|blocked by",
    re.IGNORECASE,
)
_MIN_SIZE = 4
_MAX_EXAMPLES = 3


class Scope:
    """Finds what keeps repeating in your Claude Code sessions."""

    def __init__(
        self,
        days: int = 30,
        format: str = 'markdown',
        jev: bool = False,
        root: str | None = None,
        top: int = 20,
    ):
        """`jev` sends closing lines and tool errors to TypeSafe (costs money, needs `TYPESAFE_API_KEY`)."""
        self._root = Path(root) if root else Path.home() / '.claude' / 'projects'
        self._since = datetime.now(timezone.utc) - timedelta(days=days)
        self._days = days
        self._format = format
        self._jev = jev
        self._top = top

    def prompts(self) -> str:
        """Prompts you keep typing."""
        return self._out(self._report_prompts)

    def commands(self) -> str:
        """Shell commands you keep running by hand with `!`."""
        return self._out(self._report_commands)

    def asks(self) -> str:
        """Turn endings that were answered with a bare go-ahead."""
        return self._out(self._report_asks)

    def fails(self) -> str:
        """Tool calls that keep failing, with denials apart."""
        return self._out(self._report_fails)

    def _out(self, build) -> str:
        renderers = {'markdown': render.markdown, 'json': render.json_document}
        if self._format not in renderers:
            raise findings.RepeatOScopeError(f'Unknown format `{self._format}`, expected markdown or json!')
        return renderers[self._format](build(), top=self._top)

    def _report_prompts(self) -> Report:
        return self._clustered('prompts', 'prompt', transcripts.iter_prompts(root=self._root, since=self._since))

    def _report_commands(self) -> Report:
        return self._clustered('commands', 'command', transcripts.iter_commands(root=self._root, since=self._since))

    def _report_asks(self) -> Report:
        ends = list(transcripts.iter_turn_ends(root=self._root, since=self._since))
        wasted = [e for e in ends if _GO_AHEAD.match(re.sub(r'\s+', ' ', e.reply.lower().strip()))]
        replies = collections.Counter(e.reply.lower().strip() for e in wasted)
        closings = collections.Counter(e.closing for e in wasted)
        if self._jev and closings:
            distinct = list(closings)
            kind_of = dict(zip(distinct, judge.kinds_of_asks(distinct)))
            groups = collections.defaultdict(collections.Counter)
            for c, n in closings.items():
                groups[kind_of[c]][c] = n
        else:
            groups = {None: closings} if closings else {}
        found = [
            Finding(
                # Not from the top closing line: exact phrasings rarely repeat, the kind of ask does.
                id=findings.finding_id(report='asks', top_variant=f'*{label or ""}'),
                kind='ask',
                total=group.total(),
                variants=[Variant(c, n) for c, n in group.most_common()],
                examples=[c for c, _ in group.most_common(_MAX_EXAMPLES)],
                label=label,
            )
            for label, group in sorted(groups.items(), key=lambda kv: -kv[1].total())
        ]
        _LOGGER.info('Found `%s` wasted turn endings.', len(wasted))
        return Report(
            name='asks',
            days=self._days,
            jev=self._jev,
            totals={
                'turn_endings': len(ends),
                'wasted': len(wasted),
                'not_questions': sum('?' not in e.closing[-3:] for e in wasted),
            },
            findings=found,
            note='Replies: ' + ', '.join(f'{n} × {r}' for r, n in replies.most_common(8)),
        )

    def _report_fails(self) -> Report:
        failed, denied = collections.defaultdict(list), collections.defaultdict(list)
        for f in transcripts.iter_tool_failures(root=self._root, since=self._since):
            key = cluster.normalise(f'{f.tool}: {f.error}')
            (denied if _DENIED.search(f.error) else failed)[key].append(f)
        failure_clusters, failure_noise = cluster.rank(_repeated(failed), min_size=_MIN_SIZE)
        note, label = '', None
        if self._jev and failure_clusters:
            # One example per group: the first failure behind its most frequent variant.
            real = judge.real_failures([failed[c.variants[0][0]][0] for c in failure_clusters])
            note = f'Jev judged {sum(not r for r in real)} group(s) as noise on stderr and left them out.'
            failure_clusters, label = [c for c, r in zip(failure_clusters, real) if r], 'real'
        denial_clusters, denial_noise = cluster.rank(_repeated(denied), min_size=_MIN_SIZE)
        originals = {
            k: [f'{f.tool}: {f.attempt} → {f.error}' for f in fs] for k, fs in (failed | denied).items()
        }
        found = [
            findings.from_cluster(report='fails', kind='failure', cluster=c, originals=originals, label=label)
            for c in failure_clusters
        ] + [findings.from_cluster(report='fails', kind='denial', cluster=c, originals=originals) for c in denial_clusters]
        return Report(
            name='fails',
            days=self._days,
            jev=self._jev,
            totals={
                'items': sum(map(len, failed.values())),
                'groups': len(failure_clusters),
                'one_offs': failure_noise,
                'denials': sum(map(len, denied.values())),
                'denial_groups': len(denial_clusters),
                'denial_one_offs': denial_noise,
            },
            findings=found,
            note=note,
        )

    def _clustered(self, name: str, kind: str, texts) -> Report:
        originals = collections.defaultdict(list)
        for text in texts:
            originals[cluster.normalise(text)].append(text)
        clusters, noise = cluster.rank(_repeated(originals), min_size=_MIN_SIZE)
        _LOGGER.info('Ranked `%s` %s into `%s` groups.', sum(map(len, originals.values())), name, len(clusters))
        return Report(
            name=name,
            days=self._days,
            jev=self._jev,
            totals={'items': sum(map(len, originals.values())), 'groups': len(clusters), 'one_offs': noise},
            findings=[findings.from_cluster(report=name, kind=kind, cluster=c, originals=originals) for c in clusters],
        )


def _repeated(groups: dict[str, list]) -> list[str]:
    """Returns each key once per item behind it, the shape `cluster.rank` counts."""
    return [k for k, items in groups.items() for _ in items]


def main() -> None:
    import sys

    import fire
    from rich.console import Console
    from rich.logging import RichHandler

    # Findings quote prompts in any language; a legacy Windows code page must not crash the report.
    sys.stdout.reconfigure(encoding='utf-8')
    # The report goes to stdout so it can be piped or saved; progress goes to stderr.
    handler = RichHandler(console=Console(stderr=True), show_path=False)
    logging.basicConfig(level=logging.INFO, format='%(message)s', handlers=[handler])
    # huggingface_hub logs an HTTP request on every model load.
    logging.getLogger('httpx').setLevel(logging.WARNING)
    fire.Fire(Scope)
