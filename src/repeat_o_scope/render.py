"""Turns a report into markdown for people or JSON for the skill."""

import dataclasses
import json
import re
from collections.abc import Callable
from datetime import datetime, timezone

from repeat_o_scope.audit import Audit
from repeat_o_scope.doctor import Check
from repeat_o_scope.findings import Finding, Report
from repeat_o_scope.usage import Row, Usage
from repeat_o_scope.waste import Measurement, Tally

_CHECK_LABELS = {
    'effort': 'Effort',
    'skill_overrides': 'Skill overrides',
    'skills': 'Enabled skills',
    'status_line': 'Status line',
    'scope_rule': 'Scope rule',
    'read_once_rule': 'Read-once rule',
    'idle_compact': 'Idle compact',
    'autocompact_override': 'Auto-compact override',
}
_KIND_LABELS = {
    'refused_edit': 'Refused edits',
    'reread': 'Re-reads',
    'failed': 'Failed calls',
    'denied': 'Denied tools',
    'bare_yes': 'Bare go-aheads',
    'cache_rebuild': 'Expired cache',
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
        'findings': [finding_dict(f) for f in report.findings],
    }
    return json.dumps(doc, ensure_ascii=False, indent=2)


def finding_dict(f: Finding) -> dict:
    """Returns the finding as a dict; `cost` and `share` only when it has them, as only audit findings do."""
    return {k: v for k, v in dataclasses.asdict(f).items() if v is not None or k not in ('cost', 'share')}


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
    # Long-context and rebuild turns waste only part of a call, so they are not counted as wasted turns.
    long, rebuilt = m.waste['long_context'].turns, m.waste['cache_rebuild'].turns
    wasted = sum(t.turns for t in m.waste.values()) - long - rebuilt
    share = _share(sum(t.weight for t in m.waste.values()), m.total)
    return (
        f'Last {m.days} days: {wasted} of {m.total.turns} turns wasted, '
        f'{rebuilt} more rebuilt an expired cache and {long} ran past half their context: {share:.1%} of your usage.'
    )


def _measurement_header(*, price: float | None) -> list[str]:
    return ['Kind', 'Turns', 'Share of usage', 'Tokens'] + (['€'] if price is not None else [])


def _measurement_rows(m: Measurement, *, price: float | None) -> list[list[str]]:
    return [
        [_KIND_LABELS[kind], str(t.turns), f'{_share(t.weight, m.total):.1%}', f'{t.tokens:,}']
        + ([f'€{_eur(t.weight, price=price):.2f}'] if price is not None else [])
        for kind, t in sorted(m.waste.items(), key=lambda kv: -kv[1].tokens)
    ]


def measurement_json(m: Measurement, *, price: float | None) -> str:
    """Returns the measurement as JSON, with `eur` only when `price` is set."""
    doc = {
        'report': 'measure',
        'days': m.days,
        'generated_at': datetime.now(timezone.utc).isoformat(),
    } | _measurement_doc(m, price=price)
    return json.dumps(doc, ensure_ascii=False, indent=2)


def _measurement_doc(m: Measurement, *, price: float | None) -> dict:
    def tally(t: Tally) -> dict:
        return dataclasses.asdict(t) | ({'eur': round(_eur(t.weight, price=price), 2)} if price is not None else {})

    return {
        'total': tally(m.total),
        'waste': {kind: tally(t) | {'share': _share(t.weight, m.total)} for kind, t in m.waste.items()},
    }


def usage_markdown(u: Usage, *, price: float | None) -> str:
    """Returns the usage per model as a markdown table, with euros only when `price` is set."""
    header = _usage_header(price=price)
    lines = [
        '# Usage',
        '',
        f'_{_usage_headline(u)}_',
        '',
        '| ' + ' | '.join(header) + ' |',
        '|---|' + '---:|' * (len(header) - 1),
    ]
    lines += ['| ' + ' | '.join(row) + ' |' for row in _usage_rows(u, number='{:,}'.format, price=price)]
    return '\n'.join(lines)


def usage_table(u: Usage, *, price: float | None) -> str:
    """Returns the usage per model as a rich table for a terminal, with euros only when `price` is set."""
    from rich.console import Console
    from rich.table import Table

    table = Table(title='Usage', caption=_usage_headline(u))
    for i, column in enumerate(_usage_header(price=price)):
        # The model is the row's name: narrow the numbers before cutting it.
        table.add_column(column, justify='left' if i == 0 else 'right', no_wrap=i == 0)
    rows = _usage_rows(u, number=_compact, price=price)
    for i, row in enumerate(rows):
        table.add_row(*row, end_section=i == len(rows) - 2)
    console = Console()
    with console.capture() as captured:
        console.print(table)
    return captured.get()


def usage_json(u: Usage, *, price: float | None) -> str:
    """Returns the usage per model as JSON, with `eur` only when `price` is set."""
    def row(r: Row) -> dict:
        return (
            dataclasses.asdict(r)
            | {'tokens': r.tokens, 'share': _share(r.weight, u.total)}
            | ({'eur': round(_eur(r.weight, price=price), 2)} if price is not None else {})
        )

    doc = {
        'report': 'usage',
        'days': u.days,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'total': row(u.total),
        'models': [row(r) for r in u.models],
    }
    return json.dumps(doc, ensure_ascii=False, indent=2)


def _usage_headline(u: Usage) -> str:
    t = u.total
    return f'Last {u.days} days: {t.sessions:,} sessions, {t.turns:,} turns, {t.tokens:,} tokens.'


def _compact(n: int) -> str:
    """Returns `n` as 1.4B, 26.2M or 3.1k: a session's token counts run to ten digits and overflow a terminal."""
    for unit, size in (('B', 10**9), ('M', 10**6), ('k', 10**3)):
        if n >= size:
            return f'{n / size:.1f}{unit}'
    return str(n)


def _usage_header(*, price: float | None) -> list[str]:
    return (
        ['Model', 'Sessions', 'Turns', 'Input', 'Cache writes', 'Cache reads', 'Output', 'Share of usage']
        + (['€'] if price is not None else [])
    )


def _usage_rows(u: Usage, *, number: Callable[[int], str], price: float | None) -> list[list[str]]:
    return [
        [
            r.model,
            f'{r.sessions:,}',
            f'{r.turns:,}',
            number(r.input),
            number(r.cache_write),
            number(r.cache_read),
            number(r.output),
            f'{_share(r.weight, u.total):.1%}',
        ]
        + ([f'€{_eur(r.weight, price=price):.2f}'] if price is not None else [])
        for r in [*u.models, u.total]
    ]


def audit_json(a: Audit) -> str:
    """Returns the audit as JSON for the skill: measurement, findings with their cost, and settings checks."""
    doc = {
        'report': 'audit',
        'days': a.measurement.days,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'judge': a.judge,
        'note': a.note,
    } | _measurement_doc(a.measurement, price=None) | {
        'findings': [finding_dict(f) for f in a.findings],
        'checks': [dataclasses.asdict(c) for c in a.checks],
    }
    return json.dumps(doc, ensure_ascii=False, indent=2)


def audit_markdown(a: Audit, *, top: int) -> str:
    """Returns the audit as markdown: the headline, the costliest `top` findings, then failing checks."""
    lines = ['# Audit', '', f'_{_measurement_headline(a.measurement)}_', '', a.note]
    wasted = [f for f in a.findings if f.cost is not None]
    for i, f in enumerate(wasted[:top], start=1):
        lines.append(f'{i}. **{f.share:.1%}** {f.kind}, {f.cost.turns} turns: {_shown(f.variants[0].text)}')
    lines += ['', _checks_headline(a.checks)] + [f'- {c.name}: {c.fix}' for c in a.checks if not c.ok]
    return '\n'.join(lines)


def _share(weight: float, total: Tally) -> float:
    return weight / total.weight if total.weight else 0.0


def _eur(weight: float, *, price: float) -> float:
    return weight / 1e6 * price


def checks_markdown(checks: list[Check]) -> str:
    """Returns the doctor's checks as a markdown table."""
    lines = ['# Settings doctor', '', f'_{_checks_headline(checks)}_', '', '| Check | OK | Found | Fix |', '|---|---|---|---|']
    lines += ['| ' + ' | '.join(row) + ' |' for row in _checks_rows(checks)]
    return '\n'.join(lines)


def checks_table(checks: list[Check]) -> str:
    """Returns the doctor's checks as a rich table for a terminal."""
    from rich.console import Console
    from rich.table import Table

    table = Table(title='Settings doctor', caption=_checks_headline(checks))
    for column in ('Check', 'OK', 'Found', 'Fix'):
        table.add_column(column)
    for row in _checks_rows(checks):
        table.add_row(*row)
    console = Console()
    with console.capture() as captured:
        console.print(table)
    return captured.get()


def checks_json(checks: list[Check]) -> str:
    """Returns the doctor's checks as JSON."""
    doc = {
        'report': 'doctor',
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'checks': [dataclasses.asdict(c) for c in checks],
    }
    return json.dumps(doc, ensure_ascii=False, indent=2)


def _checks_headline(checks: list[Check]) -> str:
    return f'{sum(not c.ok for c in checks)} of {len(checks)} checks to fix.'


def _checks_rows(checks: list[Check]) -> list[list[str]]:
    return [[_CHECK_LABELS[c.name], '✓' if c.ok else '✗', c.found, c.fix] for c in checks]


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
