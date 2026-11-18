# repeat-o-scope

Logs your Claude Code work, finds the prompts you keep repeating, and helps automate them away.

Most Claude Code tokens go on wasted turns rather than on the work itself: a wrong guess, a retry, a re-read.
[Why, and the set-up that cuts them](https://github.com/kimvais/repeat-o-scope/blob/main/docs/why.md). repeat-o-scope finds those turns in your own transcripts.

## Install

```
uv tool install repeat-o-scope
repeat-o-scope install
```

`install` copies the `/repeat-o-scope` skill to `~/.claude/skills/` and registers the worklog Stop hook in
`~/.claude/settings.json`. It replaces an old `worklog.ps1` Stop hook, backs `settings.json` up once to
`settings.json.repeat-o-scope.bak`, and refuses to touch a settings file it can't parse. Running it again
changes nothing. `repeat-o-scope uninstall` removes both.

The hook runs `repeat-o-scope-worklog` by name, so uv's tool directory must be on the `PATH` Claude Code sees.
If `repeat-o-scope` isn't found after installing, run `uv tool update-shell` and open a new terminal.

## The skill

In Claude Code, run `/repeat-o-scope`, or ask where your usage goes or what you keep repeating. In one pass, the
skill:
1. Runs `repeat-o-scope audit --format json --jev`: what was wasted, on what, what it cost, and your settings.
2. Judges the findings with a Haiku subagent if no judge key is set (see [Judges](#judges)).
3. Merges findings with one root cause into themes, and picks at most four by share of usage.
4. Drafts a concrete fix for each: a CLAUDE.md line, a setting, a hook, a shell function, a script fix.
5. Asks you once, applies only what you pick, and declines the rest so they don't come back.

## `audit`

```
repeat-o-scope audit      # measure + the findings behind the waste + repeats + doctor, as JSON
```

`audit` runs `measure`, groups the wasted calls of each kind by what they were about (the tool and its error,
the re-read file, the closing line a bare go-ahead answered), and gives every group its turns, tokens and
share of your usage, costliest first. Two alike wasted calls make a group; the rest of a kind is one group,
so the shares add up to the measured waste. Expired caches and long contexts have nothing to group: the
`doctor` checks, which `audit` includes, are what point at their fixes. `prompts` and `commands` findings come
along with counts only.

JSON is the default (`--format markdown` for people). `--jev` judges the groups, `decline` and `--all` work as
for the reports, and every run writes the measurement to `<user data dir>/baseline/<date>.json` for a later
before/after.

## Reports

```
repeat-o-scope prompts    # prompts you keep typing
repeat-o-scope commands   # `!` shell commands you keep running by hand
repeat-o-scope asks       # Claude turn endings you answered with a bare "ship" / "go" / "yes"
repeat-o-scope fails      # tool calls that keep failing, with denials listed apart
repeat-o-scope measure    # turns wasted per kind, and their share of your usage
repeat-o-scope usage      # sessions, turns and tokens per model
```

Each report reads the transcripts under `~/.claude/projects` from the last 30 days (`--days`), and prints
markdown on stdout (`--top` groups, default 20). Progress logging goes to stderr; `measure`, `audit` and `usage` also
show a progress bar there while they read the transcripts.

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

### `usage`

`usage` counts what you used, wasted or not: per model, the sessions, turns (API calls) and input, cache write,
cache read and output tokens, plus each model's share of your usage, weighted as in `measure`. Subagents count
towards their parent's session. It prints a table for the terminal; `--format markdown` or `--format json` gives
agents plain text, and `--price` adds €.

## Doctor

```
repeat-o-scope doctor     # your settings and CLAUDE.md against the checklist in docs/why.md
```

`doctor` reads config, not transcripts, and says what to change, failing checks first:

- **Effort:** `effortLevel`, top-level or per model under `modelSettings`, is `low` or `medium`.
- **Skill overrides:** `skillOverrides` turns at least one skill `off`.
- **Enabled skills:** at most 30, counted from `~/.claude/skills`, `.claude/skills` and enabled plugins. A
  plugin skill is off only as `plugin:skill` in `skillOverrides`. A skill with `disable-model-invocation: true`
  runs only as a `/command`, keeps its description out of context and isn't counted.
- **Status line:** `statusLine` runs a command.
- **Scope rule** and **read-once rule:** a `CLAUDE.md` (`~/.claude/`, the current directory or its `.claude/`)
  has them, matched by their key phrases.
- **Idle compact:** `CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS` is at most 100000, so idle compact runs on ordinary
  sessions during long breaks with the terminal open.
- **Auto-compact override:** no `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` below 50, unless the model is a 1M one
  (`opus[1m]`). On 200k, 25 compacts at 45,000 tokens, below what a summary leaves.

The two compaction checks follow
[claude-code-usage-analysis](https://github.com/inevolin/claude-code-usage-analysis). They read `env` in
the settings files, then your environment.

It prints a table for the terminal; `--format markdown` or `--format json` gives agents plain text. It never
writes anything and never prints settings values it doesn't check. Caveats:
- It reads the user, project and local `settings.json`, local first. Managed settings are not read.
- Built-in and claude.ai skills aren't on disk, so the skill count is a lower bound.
- It checks that a status line exists, not that it shows context % and usage %.

## Judges

Clustering can't tell a request for a go-ahead from a plain status line, or a real failure from a tool that
only wrote to stderr. `--jev` hands one example per group to a judge:

- `asks --jev` groups each closing line by kind: permission, choice, clarifying, review or report.
- `fails --jev` and `audit --jev` leave out failed-call groups that are only stderr noise.

It uses the first judge whose key is set and that answers a test question without an error:

| Judge | Keys |
|---|---|
| [Jev](https://docs.typesafe.ai) (TypeSafe System One) | `TYPESAFE_API_KEY` or `TYPESAFE_AI_API_KEY` |
| Gemini Decision 1 (Gemini Decisions API) | `GEMINI_API_KEY` |
| Clef (Cloudflare Workers AI) | `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_KEY` |

The examples are already redacted, but they leave the machine, and each call costs money. With no judge,
`asks --jev` and `fails --jev` stop with an error, and `audit --jev` reports `"judge": null`; the skill then
asks a Haiku subagent the same questions.

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
[`statusline/statusline.ps1`](https://github.com/kimvais/repeat-o-scope/blob/main/statusline/statusline.ps1) writes them to
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

Pushing a `v*` tag runs `.github/workflows/release.yml`. It checks that the tag matches the version in
`pyproject.toml`, runs the tests, builds, installs the wheel with `uv tool install` and runs
`repeat-o-scope install` against a scratch directory, and only then publishes to PyPI through trusted publishing
(no API token).

```
uv version --bump minor      # or patch; commit and merge that first
git tag "v$(uv version --short)"
git push origin "v$(uv version --short)"
```

Once, before the first release: add a pending trusted publisher at
https://pypi.org/manage/account/publishing/ with project `repeat-o-scope`, owner `kimvais`, repository
`repeat-o-scope`, workflow `release.yml` and environment `pypi`.
