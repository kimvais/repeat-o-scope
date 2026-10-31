"""repeat-o-scope: reports on the last N days of Claude Code transcripts."""

import collections
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from repeat_o_scope import cluster, transcripts

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
_SHOWN_VARIANTS = 3
_MAX_SHOWN_CHARS = 120


class Scope:
    """Finds what keeps repeating in your Claude Code sessions."""

    def __init__(self, days: int = 30, root: str | None = None, top: int = 20):
        self._root = Path(root) if root else Path.home() / '.claude' / 'projects'
        self._since = datetime.now(timezone.utc) - timedelta(days=days)
        self._days = days
        self._top = top

    def prompts(self) -> str:
        """Prompts you keep typing."""
        texts = [cluster.normalise(p) for p in transcripts.iter_prompts(root=self._root, since=self._since)]
        return self._clusters('Prompts you keep typing', texts, unit='prompts')

    def commands(self) -> str:
        """Shell commands you keep running by hand with `!`."""
        texts = [cluster.normalise(c) for c in transcripts.iter_commands(root=self._root, since=self._since)]
        return self._clusters('Commands you keep running', texts, unit='commands')

    def asks(self) -> str:
        """Turn endings that were answered with a bare go-ahead."""
        ends = list(transcripts.iter_turn_ends(root=self._root, since=self._since))
        wasted = [e for e in ends if _GO_AHEAD.match(re.sub(r'\s+', ' ', e.reply.lower().strip()))]
        replies = collections.Counter(e.reply.lower().strip() for e in wasted)
        closings = collections.Counter(e.closing for e in wasted)
        lines = [
            '# What Claude asks that always gets a yes',
            '',
            f'_Last {self._days} days: {len(wasted)} of {len(ends)} turn endings were answered with a bare go-ahead '
            f'({sum("?" not in e.closing[-3:] for e in wasted)} of them not even phrased as a question)._',
            '',
            'Replies: ' + ', '.join(f'{n} × {r}' for r, n in replies.most_common(8)),
            '',
            '## Closing lines',
            '',
            *(f'- {n} × {_shown(c)}' for c, n in closings.most_common(self._top)),
        ]
        _LOGGER.info('Found `%s` wasted turn endings.', len(wasted))
        return '\n'.join(lines)

    def fails(self) -> str:
        """Tool calls that keep failing, with denials apart."""
        failed, denied = [], []
        for f in transcripts.iter_tool_failures(root=self._root, since=self._since):
            (denied if _DENIED.search(f.error) else failed).append(cluster.normalise(f'{f.tool}: {f.error}'))
        return (
            self._clusters('Tool calls that keep failing', failed, unit='failures')
            + '\n\n'
            + self._clusters('Denied', denied, unit='denials', level='##')
        )

    def _clusters(self, title: str, texts: list[str], *, unit: str, level: str = '#') -> str:
        clusters, noise = cluster.rank(texts, min_size=_MIN_SIZE)
        lines = [
            f'{level} {title}',
            '',
            f'_Last {self._days} days: {len(texts)} {unit}, {len(clusters)} groups, {noise} one-offs._',
            '',
        ]
        for i, c in enumerate(clusters[: self._top], start=1):
            variants = ' · '.join(f'{n} × {_shown(t)}' for t, n in c.variants[:_SHOWN_VARIANTS])
            lines.append(f'{i}. **{c.total}**: {variants}')
        _LOGGER.info('Ranked `%s` %s into `%s` groups.', len(texts), unit, len(clusters))
        return '\n'.join(lines)


def _shown(text: str) -> str:
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:_MAX_SHOWN_CHARS] + '…' if len(text) > _MAX_SHOWN_CHARS else text


def main() -> None:
    import fire
    from rich.console import Console
    from rich.logging import RichHandler

    # The report goes to stdout so it can be piped or saved; progress goes to stderr.
    handler = RichHandler(console=Console(stderr=True), show_path=False)
    logging.basicConfig(level=logging.INFO, format='%(message)s', handlers=[handler])
    # huggingface_hub logs an HTTP request on every model load.
    logging.getLogger('httpx').setLevel(logging.WARNING)
    fire.Fire(Scope)
