"""Turns a report into markdown for people or JSON for the skill."""

import dataclasses
import json
import re
from datetime import datetime, timezone

from repeat_o_scope.findings import Finding, Report

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
