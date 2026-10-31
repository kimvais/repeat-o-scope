# repeat-o-scope

Logs your Claude Code work, finds the prompts you keep repeating, and helps automate them away.

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
15-minute gap) to `<Documents>/worklog/YYYY-MM-DD.jsonl`. A stretch is re-appended with growing totals
as it goes on, so **readers keep the last line per `@id`**.

Each line records `cwd`, `repo`, `branch`, `started`, `last_stop`, `turns`, `tool_uses`, the files Claude
edited, your last 10 prompts (cut to 200 characters, credentials redacted) and the transcript path.

`repeat-o-scope install` registers it. Set `REPEAT_O_SCOPE_LOG_DIR` to log somewhere other than `<Documents>/worklog`. Per-session state lives in
`~/.claude/hooks/worklog-state`.

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
