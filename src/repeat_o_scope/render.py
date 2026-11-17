"""Turns a report into markdown for people or JSON for the skill."""

import dataclasses
import json
import re
from datetime import datetime, timezone

from repeat_o_scope.findings import Finding, Report
from repeat_o_scope.waste import Measurement, Tally

_KIND_LABELS = {
    'refused_edit': 'Refused edits',
    'reread': 'Re-reads',
    'failed': 'Failed calls',
    'denied': 'Denied tools',
    'bare_yes': 'Bare go-aheads',
    'long_context': 'Past half context',
}
_SHOWN_VARIANTS = 3
_MAX_SHOWN_CHARS = 120
_TITLES = {
    'prompts': ('Prompts you keep typing', 'prompts'),
    'commands': ('Commands you keep running', 'commands'),
    'fails': ('Tool calls that keep failing', 'failures'),
}


def json_document(report: Report, *, top: int) -> str:
    """Returns the report as JSON with every finding; `top` only limits markdown."""
    doc = {
        'report': report.name,
        'days': report.days,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'jev': report.jev,
        'totals': report.totals,
        'findings': [dataclasses.asdict(f) for f in report.findings],
    }
    return json.dumps(doc, ensure_ascii=False, indent=2)


def markdown(report: Report, *, top: int) -> str:
    """Returns the report as markdown, showing at most `top` findings per section."""
    if report.name == 'asks':
        return _asks(report, top=top)
    title, unit = _TITLES[report.name]
    t = report.totals
    text = _section(
        f'# {title}',
        [f for f in report.findings if f.kind != 'denial'],
        summary=f'{t["items"]} {unit}, {t["groups"]} groups, {t["one_offs"]} one-offs.',
        days=report.days,
        note=report.note,
        top=top,
    )
    if report.name == 'fails':
        text += '\n\n' + _section(
            '## Denied',
            [f for f in report.findings if f.kind == 'denial'],
            summary=f'{t["denials"]} denials, {t["denial_groups"]} groups, {t["denial_one_offs"]} one-offs.',
            days=report.days,
            note='',
            top=top,
        )
    return text


def measurement_markdown(m: Measurement, *, price: float | None) -> str:
    """Returns the measurement as a markdown table, with euros only when `price` is set."""
    header = _measurement_header(price=price)
    lines = [
        '# Wasted turns',
        '',
        f'_{_measurement_headline(m)}_',
        '',
        '| ' + ' | '.join(header) + ' |',
        '|---|' + '---:|' * (len(header) - 1),
    ]
    lines += ['| ' + ' | '.join(row) + ' |' for row in _measurement_rows(m, price=price)]
    return '\n'.join(lines)


def measurement_table(m: Measurement, *, price: float | None) -> str:
    """Returns the measurement as a rich table for a terminal, with euros only when `price` is set."""
    from rich.console import Console
    from rich.table import Table

    table = Table(title='Wasted turns', caption=_measurement_headline(m))
    for i, column in enumerate(_measurement_header(price=price)):
        table.add_column(column, justify='left' if i == 0 else 'right')
    for row in _measurement_rows(m, price=price):
        table.add_row(*row)
    console = Console()
    with console.capture() as captured:
        console.print(table)
    return captured.get()


def _measurement_headline(m: Measurement) -> str:
    # A long-context turn wastes only its excess context, so it is not counted as a wasted turn.
    long = m.waste['long_context'].turns
    wasted = sum(t.turns for t in m.waste.values()) - long
    share = _share(sum(t.weight for t in m.waste.values()), m.total)
    return (
        f'Last {m.days} days: {wasted} of {m.total.turns} turns wasted, '
        f'and {long} more ran past half their context: {share:.1%} of your usage.'
    )


def _measurement_header(*, price: float | None) -> list[str]:
    return ['Kind', 'Turns', 'Share of usage', 'Tokens'] + (['€'] if price is not None else [])


def _measurement_rows(m: Measurement, *, price: float | None) -> list[list[str]]:
    return [
        [_KIND_LABELS[kind], str(t.turns), f'{_share(t.weight, m.total):.1%}', f'{t.tokens:,}']
        + ([f'€{_eur(t, price=price):.2f}'] if price is not None else [])
        for kind, t in m.waste.items()
    ]


def measurement_json(m: Measurement, *, price: float | None) -> str:
    """Returns the measurement as JSON, with `eur` only when `price` is set."""
    def tally(t: Tally) -> dict:
        return dataclasses.asdict(t) | ({'eur': round(_eur(t, price=price), 2)} if price is not None else {})

    doc = {
        'report': 'measure',
        'days': m.days,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'total': tally(m.total),
        'waste': {kind: tally(t) | {'share': _share(t.weight, m.total)} for kind, t in m.waste.items()},
    }
    return json.dumps(doc, ensure_ascii=False, indent=2)


def _share(weight: float, total: Tally) -> float:
    return weight / total.weight if total.weight else 0.0


def _eur(t: Tally, *, price: float) -> float:
    return t.weight / 1e6 * price


def _section(heading: str, found: list[Finding], *, days: int, note: str, summary: str, top: int) -> str:
    lines = [heading, '', f'_Last {days} days: {summary}_ {note}'.rstrip(), '']
    for i, f in enumerate(found[:top], start=1):
        variants = ' · '.join(f'{v.count} × {_shown(v.text)}' for v in f.variants[:_SHOWN_VARIANTS])
        lines.append(f'{i}. **{f.total}**: {variants}')
    return '\n'.join(lines)


def _asks(report: Report, *, top: int) -> str:
    t = report.totals
    lines = [
        '# What Claude asks that always gets a yes',
        '',
        f'_Last {report.days} days: {t["wasted"]} of {t["turn_endings"]} turn endings were answered with a bare '
        f'go-ahead ({t["not_questions"]} of them not even phrased as a question)._',
        '',
        report.note,
    ]
    for f in report.findings:
        lines += ['', f'## {f.label}: {f.total}' if f.label else '## Closing lines', '']
        lines += [f'- {v.count} × {_shown(v.text)}' for v in f.variants[:top]]
    return '\n'.join(lines)


def _shown(text: str) -> str:
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:_MAX_SHOWN_CHARS] + '…' if len(text) > _MAX_SHOWN_CHARS else text
