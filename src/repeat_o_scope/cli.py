"""repeat-o-scope: reports on the last N days of Claude Code transcripts."""

import collections
import dataclasses
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from repeat_o_scope import audit, cluster, declined, doctor, findings, install, judge, judges, render, transcripts, waste
from repeat_o_scope.findings import Finding, Report, Variant

_LOGGER = logging.getLogger(__name__)

_MIN_SIZE = 4
_MAX_EXAMPLES = 3


class Scope:
    """Finds what keeps repeating in your Claude Code sessions."""

    def __init__(
        self,
        all: bool = False,
        claude_dir: str | None = None,
        data_dir: str | None = None,
        days: int = 30,
        format: str | None = None,
        jev: bool = False,
        price: float | None = None,
        root: str | None = None,
        top: int = 20,
    ):
        """`jev` sends closing lines and tool errors to TypeSafe (costs money, needs `TYPESAFE_API_KEY`).

        `all` shows findings you declined, too.
        """
        self._all = all
        self._claude_dir = Path(claude_dir) if claude_dir else Path.home() / '.claude'
        self._data_dir = Path(data_dir) if data_dir else declined.default_dir()
        self._root = Path(root) if root else self._claude_dir / 'projects'
        self._since = datetime.now(timezone.utc) - timedelta(days=days)
        self._days = days
        self._format = format
        self._jev = jev
        self._price = price
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

    def measure(self) -> str:
        """Turns wasted per kind, and their share of your usage. `--price` adds € per million input tokens."""
        # No --format means a person at a terminal; agents ask for markdown or json.
        renderers = {
            None: render.measurement_table,
            'markdown': render.measurement_markdown,
            'json': render.measurement_json,
        }
        if self._format not in renderers:
            raise findings.RepeatOScopeError(f'Unknown format `{self._format}`, expected markdown or json!')
        m = waste.measure(days=self._days, root=self._root, since=self._since)
        return renderers[self._format](m, price=self._price)

    def doctor(self) -> str:
        """Checks your settings and CLAUDE.md against the budget checklist in docs/why.md."""
        # Not through `_out`: checks are not findings to remember or decline.
        renderers = {None: render.checks_table, 'markdown': render.checks_markdown, 'json': render.checks_json}
        if self._format not in renderers:
            raise findings.RepeatOScopeError(f'Unknown format `{self._format}`, expected markdown or json!')
        return renderers[self._format](doctor.diagnose(claude_dir=self._claude_dir, project_dir=Path.cwd()))

    def install(self) -> str:
        """Installs the repeat-o-scope skill and the worklog Stop hook into ~/.claude."""
        return '\n'.join(install.install(claude_dir=self._claude_dir)) or 'Already installed.'

    def uninstall(self) -> str:
        """Removes the repeat-o-scope skill and the worklog Stop hook from ~/.claude."""
        return '\n'.join(install.uninstall(claude_dir=self._claude_dir)) or 'Nothing to remove.'

    def decline(self, finding_id: str) -> str:
        """Leaves a finding from the last run out of future reports."""
        variants = declined.decline(data_dir=self._data_dir, finding_id=finding_id)
        return f'Declined `{finding_id}`: {", ".join(variants)}'

    def _out(self, build) -> str:
        renderers = {None: render.markdown, 'markdown': render.markdown, 'json': render.json_document}
        if self._format not in renderers:
            raise findings.RepeatOScopeError(f'Unknown format `{self._format}`, expected markdown or json!')
        report = build()
        declined.remember_last(data_dir=self._data_dir, report=report)
        if not self._all:
            report = dataclasses.replace(
                report,
                findings=declined.drop_declined(data_dir=self._data_dir, findings=report.findings),
            )
        return renderers[self._format](report, top=self._top)

    def _report_prompts(self) -> Report:
        return self._clustered('prompts', 'prompt', transcripts.iter_prompts(root=self._root, since=self._since))

    def _report_commands(self) -> Report:
        return self._clustered('commands', 'command', transcripts.iter_commands(root=self._root, since=self._since))

    def _report_asks(self) -> Report:
        ends = list(transcripts.iter_turn_ends(root=self._root, since=self._since))
        wasted = [e for e in ends if transcripts.is_go_ahead(e.reply)]
        replies = collections.Counter(e.reply.lower().strip() for e in wasted)
        closings = collections.Counter(e.closing for e in wasted)
        if self._jev and closings:
            distinct = list(closings)
            kind_of = dict(zip(distinct, judge.kinds_of_asks(distinct, judge=_open_judge())))
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
            (denied if transcripts.is_denial(f.error) else failed)[key].append(f)
        failure_clusters, failure_noise = cluster.rank(audit.repeated(failed), min_size=_MIN_SIZE)
        note, label = '', None
        if self._jev and failure_clusters:
            # One example per group: the first failure behind its most frequent variant.
            judged_by = _open_judge()
            firsts = [failed[c.variants[0][0]][0] for c in failure_clusters]
            real = judge.real_failures([f'{f.tool}: {f.attempt} → {f.error}' for f in firsts], judge=judged_by)
            note = f'{judged_by.name} judged {sum(not r for r in real)} group(s) as noise on stderr and left them out.'
            failure_clusters, label = [c for c, r in zip(failure_clusters, real) if r], 'real'
        denial_clusters, denial_noise = cluster.rank(audit.repeated(denied), min_size=_MIN_SIZE)
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
        return audit.clustered(name, kind, texts, days=self._days, jev=self._jev)


def _open_judge() -> judges.Judge:
    if (opened := judges.open_judge()) is None:
        raise findings.RepeatOScopeError('No judge available: set a Jev, Gemini or Cloudflare key!')
    return opened


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
