# repeat-o-scope

Logs your Claude Code work, finds the prompts you keep repeating, and helps automate them away.

## Worklog hook

A Claude Code `Stop` hook that appends one JSON line per stretch of work (a session's activity until a
15-minute gap) to `<Documents>/worklog/YYYY-MM-DD.jsonl`. A stretch is re-appended with growing totals
as it goes on, so **readers keep the last line per `@id`**.

Each line records `cwd`, `repo`, `branch`, `started`, `last_stop`, `turns`, `tool_uses`, the files Claude
edited, your last 10 prompts (cut to 200 characters, credentials redacted) and the transcript path.

### Install

```
uv tool install git+ssh://git@github.com/kimvais/repeat-o-scope
```

Then add the hook to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "Stop": [{"hooks": [{"type": "command", "command": "repeat-o-scope-worklog"}]}]
  }
}
```

Set `REPEAT_O_SCOPE_LOG_DIR` to log somewhere other than `<Documents>/worklog`. Per-session state lives in
`~/.claude/hooks/worklog-state`.

### Replacing `worklog.ps1`

This is a port of the PowerShell hook `worklog.ps1`. It reads the same state directory, so session
indexes (`s0494.1`, ...) carry on where the PowerShell hook stopped. Swap the hook command and remove
the old one; running both at once would log every stretch twice.

Two differences from `worklog.ps1`:
- The JSON-LD vocabulary is `urn:repeat-o-scope:worklog#` instead of `urn:vainu:worklog#`.
- Timestamps have 6 fractional digits instead of 7.

## Development

```
uv run pytest
```
