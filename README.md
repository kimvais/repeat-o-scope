# repeat-o-scope

Logs your Claude Code work, finds the prompts you keep repeating, and helps automate them away.

Most Claude Code tokens go on wasted turns rather than on the work itself: a wrong guess, a retry, a re-read.
[Why, and the set-up that cuts them](docs/why.md). repeat-o-scope finds those turns in your own transcripts.

## Install

```
uv tool install repeat-o-scope
repeat-o-scope install
```

`install` copies the `/repeat-o-scope` skill to `~/.claude/skills/` and registers the worklog Stop hook in
`~/.claude/settings.json`. It replaces an old `worklog.ps1` Stop hook, backs `settings.json` up once to
`settings.json.repeat-o-scope.bak`, and refuses to touch a settings file it can't parse. Running it again
changes nothing. `repeat-o-scope uninstall` removes both.

## The skill

In Claude Code, run `/repeat-o-scope`, or ask what you keep repeating. The skill:
1. Runs the four reports below as JSON.
2. Picks at most four findings, the top one from each report first.
3. Drafts a concrete fix for each: a shell function, a hook, a CLAUDE.md line, a script fix.
4. Asks you once, applies only what you pick, and declines the rest so they don't come back.

## Reports

```
repeat-o-scope prompts    # prompts you keep typing
repeat-o-scope commands   # `!` shell commands you keep running by hand
repeat-o-scope asks       # Claude turn endings you answered with a bare "ship" / "go" / "yes"
repeat-o-scope fails      # tool calls that keep failing, with denials listed apart
repeat-o-scope measure    # turns wasted per kind, and their share of your usage
```

Each report reads the transcripts under `~/.claude/projects` from the last 30 days (`--days`), and prints
markdown on stdout (`--top` groups, default 20). Progress logging goes to stderr.

- `--format json` prints every finding as JSON for the skill: an `id`, a `kind`, a `total`, the `variants`,
  up to 3 redacted `examples`, and Jev's `label`.
- `repeat-o-scope decline <id>` leaves a finding from the last run out of later reports, matched on its
  variants. `--all` shows declined findings again. That state lives in the local user data directory,
  never in a synced folder.

The window is deliberately short. Claude Code deletes transcripts after 30 days anyway, and whatever you
automated after an earlier run should drop out instead of dominating.

How it works:
- Only what you typed counts. Harness plumbing, meta messages, subagents and scripted runs
  (`entrypoint: sdk-cli`) are skipped. `fails` is the exception and includes subagents.
- Credentials are redacted before anything is grouped or printed.
- Texts are normalised: lowercase, with paths, URLs, `!`/`#` references, hashes and numbers replaced by
  placeholders.
- They are embedded locally with `model2vec` (`minishlab/potion-base-8M`, about 30 MB, downloaded once) and
  grouped with scikit-learn's HDBSCAN. Nothing leaves the machine unless you pass `--jev`.
- A group needs at least 4 occurrences.

### `measure`

`measure` counts turns, where a turn is one API call, and charges each wasted turn to one kind:

- **Refused edits:** an Edit or Write of a file Claude had not read yet.
- **Re-reads:** reading the same file range again in one context. A read after an edit of that file, or after
  compaction, doesn't count.
- **Failed calls:** any other tool error.
- **Denied tools:** calls refused by you, a hook or the permission classifier, the same ones `fails` lists
  under Denied.
- **Bare go-aheads:** the turn that answers a bare "yes" / "ship" / "go", the same replies `asks` counts.
- **Expired cache:** the first call after a gap longer than the session's cache lifetime (5 minutes, or 1 hour
  if most of its cache writes were 1-hour ones) writes the previous context to the cache again. It counts what
  that rewrite cost above a warm cache read, on calls not charged to another kind.
- **Past half context:** the context a call carried above half its window, on calls not charged to another kind.

It prints a table for the terminal. `--format markdown` or `--format json` gives agents plain text. The
headline is the wasted share of your usage. Raw tokens are a secondary column, and `--price 3.0` adds €,
priced per million input tokens. Caveats:
- Your usage limit isn't in the transcripts, so the share is the share of what you used, not of the limit.
- Tokens are weighted by the call's model's API price ratios to an input token: output 5, 5-minute cache
  writes 1.25, 1-hour cache writes 2, and cache reads 0.05 on Opus 5.5 and Sonnet 5.5, 0.025 on Fable and
  Mythos 5.1 and 5.2, and 0.1 on older models.
- A session resumed under a new session id starts a new context, so its first rebuild isn't counted.
- Cowork keeps its sessions apart. On Windows, point `--root` at them:
  `repeat-o-scope measure --root="$env:LOCALAPPDATA\Claude-Data\claude_<id>\local-agent-mode-sessions"`.
- Transcripts don't say whether a session ran with a 200k or 1M context window. Each session gets the
  smallest one that fits its largest call.

### `--jev`

Clustering can't tell a request for a go-ahead from a plain status line, or a real failure from a tool that
only wrote to stderr. `--jev` hands those calls to [Jev](https://docs.typesafe.ai) (TypeSafe System One):

- `asks --jev` groups each closing line by kind: permission, choice, clarifying, review or report.
- `fails --jev` asks about one example per group and leaves out groups that are only stderr noise.

It sends those closing lines and tool errors, already redacted, to TypeSafe, and it costs money per call. It
needs `TYPESAFE_API_KEY`:

```powershell
$env:TYPESAFE_API_KEY = op item get TypeSafe --fields "api key" --reveal
repeat-o-scope asks --jev
```

## Worklog hook

A Claude Code `Stop` hook that appends one JSON line per stretch of work (a session's activity until a
15-minute gap) to `<user data dir>/worklog/YYYY-MM-DD.jsonl` (on Windows
`%LOCALAPPDATA%\repeat-o-scope\worklog`). A stretch is re-appended with growing totals as it goes on, so
**readers keep the last line per `@id`**.

Each line records `cwd`, `repo`, `branch`, `started`, `last_stop`, `turns`, `tool_uses`, the files Claude
edited, your last 10 prompts (cut to 200 characters, credentials redacted), the transcript path, and the
rate limits (below).

`repeat-o-scope install` registers it. Set `REPEAT_O_SCOPE_LOG_DIR` to log somewhere else. The default
used to be `<Documents>/worklog`; it moved because Windows ransomware protection (Controlled Folder Access)
blocks python and pwsh from writing there. Per-session state lives in `~/.claude/hooks/worklog-state`.

### Rate limits and extra usage

Transcripts don't record whether a turn went to extra usage. Claude Code passes the 5-hour and 7-day
limits only to the status line (`rate_limits`, claude.ai subscriptions only), so
[`statusline/statusline.ps1`](statusline/statusline.ps1) writes them to
`~/.claude/hooks/worklog-state/<session_id>.rate-limits.json`, and the Stop hook copies them into the log:

- `rate_limits`: the status line's latest copy, as Claude Code gave it (`used_percentage`, `resets_at`).
- `rate_limit_peak`: the highest `used_percentage` per window during the stretch. A window at 100 means
  the stretch went to extra usage, even if the window reset before the stretch ended.

Both are `null` without that status line. The copy is as fresh as the status line's last refresh, so the
last response of a stretch can show up only at the next stop.

To use the script as is, point `statusLine` in `~/.claude/settings.json` at it:
`pwsh -NoProfile -File <repo>/statusline/statusline.ps1`. Besides the limits, it shows the branch's issue and
MR (via `glab`), context use and the repo or worktree name.

### Replacing `worklog.ps1`

This is a port of the PowerShell hook `worklog.ps1`. It reads the same state directory, so session
indexes (`s0494.1`, ...) carry on where the PowerShell hook stopped. `repeat-o-scope install` swaps the old
hook out, because running both would log every stretch twice.

Two differences from `worklog.ps1`:
- The JSON-LD vocabulary is `urn:repeat-o-scope:worklog#` instead of the old hook's own.
- Timestamps have 6 fractional digits instead of 7.

## Development

```
uv run pytest
```

## Releasing

Pushing a `v*` tag runs `.github/workflows/release.yml`, which tests, builds and publishes to PyPI through
trusted publishing (no API token). The first release needs a trusted publisher on PyPI for this repo,
workflow `release.yml` and environment `pypi`.
