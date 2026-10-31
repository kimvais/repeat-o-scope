"""Claude Code Stop hook: appends one work-log entry per stretch of work per session.

Port of `worklog.ps1`. Each line is a JSON-LD node keyed by @id (session_id + seq) and
prefixed s<index>.<seq>: index is a global per-session counter, seq increments after a
gap of `_GAP` without a Stop. Within a stretch the same @id is re-appended with the
growing totals, so a reader keeps the LAST line per @id. The log is always current, even
for sessions that never end.

Output: <log dir>/YYYY-MM-DD.jsonl (date of the stretch's first turn)
State:  <state dir>/<session_id>.json (transcript offset, seq, stretch so far)
"""

import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import platformdirs

_LOGGER = logging.getLogger(__name__)

_GAP = timedelta(minutes=15)
_MAX_PROMPTS = 10
_MAX_PROMPT_CHARS = 200
_GIT_TIMEOUT_SECONDS = 5
_EDIT_TOOLS = frozenset({'Edit', 'Write', 'MultiEdit', 'NotebookEdit'})
# Credentials pasted into a prompt must not reach the log (it may live in a synced folder):
# Google OAuth tokens and codes, GitLab, GitHub, Slack, AWS, Anthropic and OpenAI keys, private keys.
# Case-insensitive because PowerShell's -replace is.
_SECRET = re.compile(
    r'ya29\.[\w-]+|\b4/0A[\w-]+|1//0[\w-]{20,}|glpat-[\w.-]+|gh[pousr]_\w{20,}|xox[abprs]-[\w-]+'
    r'|AKIA[0-9A-Z]{16}|sk-(ant-)?[\w-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----',
    re.IGNORECASE,
)
# Tool results, slash-command plumbing and injected reminders.
_PLUMBING = re.compile(r'^\s*<(command-|local-command|system-reminder)', re.IGNORECASE)
_CONTEXT = {
    '@vocab': 'urn:repeat-o-scope:worklog#',
    'started': {'@type': 'http://www.w3.org/2001/XMLSchema#dateTime'},
    'last_stop': {'@type': 'http://www.w3.org/2001/XMLSchema#dateTime'},
    'repo': {'@type': '@id'},
    'files': {'@container': '@set'},
    'prompts': {'@container': '@list'},
}


def record_stop(
    *,
    hook: dict,
    log_dir: Path,
    now: datetime,
    state_dir: Path,
) -> None:
    """Appends the current stretch of the hook's session to the day's log."""
    sid = hook.get('session_id')
    transcript = hook.get('transcript_path')
    if not sid or not transcript or not Path(transcript).exists():
        return

    log_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / f'{sid}.json'
    state = json.loads(state_path.read_text(encoding='utf-8-sig')) if state_path.exists() else None

    if not state or now - datetime.fromisoformat(state['last_stop']) >= _GAP:
        seq = state['seq'] + 1 if state else 1
        started = now.isoformat()
        prompts, files, turns, tools = [], [], 0, 0
    else:
        seq = state['seq']
        started = state['started']
        prompts, files = list(state['prompts'] or []), list(state['files'] or [])
        turns, tools = state['turns'], state['tool_uses']
    offset = state['offset'] if state else 0
    branch = state.get('branch') if state else None

    line_no = 0
    with open(transcript, encoding='utf-8') as f:
        for line in f:
            line_no += 1
            line = line.rstrip('\r\n')
            if line_no <= offset or not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(e, dict):
                continue
            if e.get('gitBranch'):
                branch = e['gitBranch']
            content = (e.get('message') or {}).get('content')
            if e.get('type') == 'user' and not e.get('isMeta'):
                text = _prompt_text(content)
                if text and not _PLUMBING.match(text):
                    prompts.append(_clean(text))
                    turns += 1
            elif e.get('type') == 'assistant' and isinstance(content, list):
                for block in content:
                    if block.get('type') != 'tool_use':
                        continue
                    tools += 1
                    # TODO: NotebookEdit takes `notebook_path`, so notebooks never show up. Copied from worklog.ps1.
                    path = block.get('name') in _EDIT_TOOLS and block['input'].get('file_path')
                    if path and path not in files:
                        files.append(path)

    cwd = hook.get('cwd')
    repo = None
    if cwd and Path(cwd).exists():
        repo = _git(cwd, 'remote', 'get-url', 'origin')
        branch = branch or _git(cwd, 'branch', '--show-current')

    index = state.get('index') if state else None
    index = index or _next_index(state_dir)

    entry = {
        '@context': _CONTEXT,
        '@id': f'urn:claude-code:session:{sid}#{seq}',
        '@type': 'WorkStretch',
        'prefix': f's{index:04d}.{seq}',
        'index': index,
        'session_id': sid,
        'seq': seq,
        'started': started,
        'last_stop': now.isoformat(),
        'cwd': cwd,
        'repo': repo,
        'branch': branch,
        'turns': turns,
        'tool_uses': tools,
        'files': files,
        'prompts': prompts[-_MAX_PROMPTS:],
        'transcript': transcript,
    }
    day = datetime.fromisoformat(started).strftime('%Y-%m-%d')
    _append(log_dir / f'{day}.jsonl', json.dumps(entry, ensure_ascii=False, separators=(',', ':')) + '\n')

    state_path.write_text(
        json.dumps({**entry, 'prompts': prompts, 'offset': line_no}, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )


def _prompt_text(content) -> str:
    """Returns the text of a user message; empty for tool results."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ''
    return ' '.join(b['text'] for b in content if b.get('type') == 'text')


def _clean(text: str) -> str:
    """Returns the prompt on one line, redacted and cut to `_MAX_PROMPT_CHARS`."""
    text = _SECRET.sub('[REDACTED]', re.sub(r'\s+', ' ', text).strip())
    return text[:_MAX_PROMPT_CHARS] + '...' if len(text) > _MAX_PROMPT_CHARS else text


def _git(cwd: str, *args: str) -> str | None:
    """Returns git's output in `cwd`, or None when git fails or prints nothing."""
    try:
        out = subprocess.run(
            ['git', '-C', cwd, *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return (out.returncode == 0 and out.stdout.strip()) or None


def _next_index(state_dir: Path) -> int:
    """Returns the next global session index: 1, 2, ..."""
    counter = state_dir / '_counter'
    n = int(counter.read_text(encoding='utf-8-sig')) + 1 if counter.exists() else 1
    counter.write_text(f'{n}\n', encoding='utf-8')
    return n


def _append(path: Path, text: str) -> None:
    """Appends `text`, retrying briefly because several sessions can stop at once."""
    for _ in range(5):
        try:
            with path.open('a', encoding='utf-8') as f:
                f.write(text)
            return
        except OSError:
            time.sleep(0.1)


def main() -> None:
    try:
        default_log = platformdirs.user_documents_path() / 'worklog'
        record_stop(
            hook=json.load(sys.stdin),
            log_dir=Path(os.environ.get('REPEAT_O_SCOPE_LOG_DIR') or default_log),
            now=datetime.now().astimezone(),
            state_dir=Path.home() / '.claude' / 'hooks' / 'worklog-state',
        )
    except Exception:
        # Never break Claude Code over a log line.
        _LOGGER.exception('Worklog hook failed.')
