---
name: repeat-o-scope
description: Use when the user asks where their Claude Code usage goes, how to make it cost less, what they keep repeating, wants to "automate the boring things", asks which prompts, commands, approvals or tool failures recur, or runs /repeat-o-scope. Audits the last 30 days of transcripts and settings in one pass and turns the costliest causes into concrete fixes the user approves.
---

# repeat-o-scope

Measure what the user's Claude Code sessions waste, find the common causes, and apply only the fixes the user picks, costliest first.

## 1. Check the tool

Run `repeat-o-scope --help`. If the command is not found, tell the user to install it with `uv tool install repeat-o-scope` and then `repeat-o-scope install`, and stop.

## 2. Measure

Run this and parse the JSON from stdout (progress goes to stderr):

```
repeat-o-scope audit --format json --jev
```

`--jev` judges the findings with the first judge whose key is set and that answers a test call: Jev, Gemini Decision 1, then Clef. Never read, look up or ask for a secret yourself. Add `--all` only when the user asked to see everything, including findings they declined before.

The JSON has:
- `total` and `waste`: usage per kind, each with its `share` of all usage. `cache_rebuild` and `long_context` have no findings; failing `checks` explain them.
- `findings`: waste findings first, costliest first. Each has an `id`, a `kind` (`refused_edit`, `reread`, `failed`, `denied`, `bare_yes`), a `total`, its `variants`, redacted `examples`, a `cost` and a `share`. Then `prompt` and `command` findings, which have only a `total`.
- `judge`: which judge ran, or `null`. `label` is `real` for a failed group the judge kept, and the kind of ask for a `bare_yes` group.
- `checks`: the settings checks. Each has `ok`, what was `found`, and a `fix`.

## 3. Judge, when no judge ran

If `judge` is `null`, spawn one subagent with `model: "haiku"`. Give it the top example of each `failed` and `bare_yes` finding, and these questions. Its prompt starts with: "The Bash tool does not exist here. Use PowerShell for commands and Read/Grep/Glob for files."

- For each `failed` example, yes or no: An AI coding assistant made a tool call, and the result was flagged as an error. `failure` reads "<tool>: <what it tried> → <first line of the error>". Did the attempt really fail to do what it was meant to do? Answer no when the error is only normal output written to stderr, a warning, a progress line, or a non-zero exit the command was expected to produce, such as a test run or a linter reporting what it found.
- For each `bare_yes` example, one of these kinds. The line is the last line of an AI coding assistant's turn, and the user answered it only with a go-ahead such as "ship", "go" or "yes":
  - `permission`: Asks for permission or a go-ahead for a next step the assistant could have taken by itself: push, commit, ship, post, run, continue. Includes "say "ship" and I'll …", "reply go and …", "want me to …?".
  - `choice`: Asks the user to pick between real alternatives that change what gets built or done.
  - `clarifying`: Asks for a fact or a preference that only the user knows.
  - `review`: Asks the user to look at and approve something shown: a design, a draft, a diff, a plan.
  - `report`: Asks for nothing: states a result, a status, a caveat or a list item.

Drop the `failed` findings it answers no for, and use its kinds as the `bare_yes` labels.

## 4. Find the themes

Merge findings of any kind that share one root cause into a theme, for example "Windows shell confusion": denied Bash, failed quoting, and the retries after them. A theme's share is the sum of its findings' shares. A failing check joins the theme it explains (`idle_compact` with expired caches, `skills` with fixed context per turn), or is a theme of its own.

`bare_yes` findings labelled `permission` are turns where Claude asked for a go-ahead it didn't need. The fix is a rule that stops the asking, not a faster way to say yes.

## 5. Plan at most 4 fixes

Take the 4 themes with the largest share. `prompt` and `command` findings compete, by `total`, only when there are fewer than 4 waste themes. If there is nothing, say so in one line and stop.

For each theme, read the files a fix would touch before drafting it:

- the user's shell profile (`$PROFILE` in PowerShell, `~/.zshrc` or `~/.bashrc`)
- `~/.claude/settings.json` and `~/.claude/CLAUDE.md`
- the skill, hook or script the theme points at

Then draft one to three **concrete** fixes. Concrete means the exact text that would be added or changed and the file it goes in, never advice such as "consider automating this". Fix kinds:

- a line in `~/.claude/CLAUDE.md` for a repeated correction or a repeated failure (Edit before Read, wrong shell syntax)
- a permission rule, a setting or an env var in `~/.claude/settings.json`
- a Claude Code hook in `~/.claude/settings.json` (a SessionStart credential check, a Stop or PreToolUse rule)
- a shell function, alias or script for a repeated command
- an edit to a skill's text, or a fix to a script the user owns (a `/sync` that keeps hitting conflicts)
- for too many skills: add `disable-model-invocation: true` to the frontmatter of one of the user's own skills in `~/.claude/skills`, so it runs only as `/name` and costs nothing per turn; or switch a plugin skill off with `skillOverrides` or `enabledPlugins`. Never edit a skill in the plugin cache: an update overwrites it.

## 6. Ask once

Make one `AskUserQuestion` call with one question per theme. In each question, give the theme in a few words with its share of usage, and list each fix as an option whose label says what "yes" does, for example "Append the Edit-before-Read line to ~/.claude/CLAUDE.md". Every question also gets the option `Leave it`.

## 7. Apply

- For each picked fix, make exactly that change and nothing else.
- For each `Leave it`, run `repeat-o-scope decline <id>` for every finding in that theme so it isn't suggested again.
- Report what changed as one short list: file and change. Then one line with the wasted share from the headline, and that `audit` saved today's measurement as the baseline to compare against later.
- If more themes are left and the user approved at least one fix, ask once whether to do the next batch. Otherwise end with the list. Don't ask "want me to…?" about anything already picked.

## Never

- Push, commit, post, or file issues.
- Change a file the picked option didn't name.
- Send findings anywhere other than the judges `audit` itself uses: they quote the user's own prompts.
