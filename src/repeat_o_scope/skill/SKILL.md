---
name: repeat-o-scope
description: Use when the user asks what they keep repeating in Claude Code, wants to "automate the boring things", asks which prompts, commands, approvals or tool failures recur, or runs /repeat-o-scope. Finds the repeats in the last 30 days of transcripts and turns each into a concrete fix the user approves.
---

# repeat-o-scope

Turn what keeps repeating in the user's Claude Code sessions into concrete fixes, and apply only the ones the user picks.

## 1. Check the tool

Run `repeat-o-scope --help`. If the command is not found, tell the user to install it with `uv tool install repeat-o-scope` and then `repeat-o-scope install`, and stop.

## 2. Run the four reports

Run each of these and parse the JSON from stdout (progress goes to stderr):

```
repeat-o-scope prompts --format json
repeat-o-scope commands --format json
repeat-o-scope asks --format json
repeat-o-scope fails --format json
```

- Add `--jev` only when `TYPESAFE_API_KEY` is already set in the environment. Never read, look up or ask for a secret yourself.
- Add `--all` only when the user asked to see everything, including findings they declined before.

Each finding has an `id`, a `kind` (`prompt`, `command`, `ask`, `failure`, `denial`), a `total`, its `variants`, a few redacted `examples`, and Jev's `label` when `--jev` ran.

## 3. Pick at most 4 findings

Take the top finding of each report first, then the rest by `total`, up to 4. If there are none, say so in one line and stop.

## 4. Draft fixes

For each finding, read the files a fix would touch before drafting it:

- the user's shell profile (`$PROFILE` in PowerShell, `~/.zshrc` or `~/.bashrc`)
- `~/.claude/settings.json` and `~/.claude/CLAUDE.md`
- the skill, hook or script the finding points at

Then draft one to three **concrete** fixes. Concrete means the exact text that would be added or changed and the file it goes in, never advice such as "consider automating this". Fix kinds:

- a shell function or alias for a repeated command (`gcloud auth login` becomes a function that checks first and logs in only when needed)
- a script for a repeated multi-step command
- a Claude Code hook in `~/.claude/settings.json` (a SessionStart credential check, a Stop or PreToolUse rule)
- a permission rule in `~/.claude/settings.json`
- a line in `~/.claude/CLAUDE.md` for a repeated correction or a repeated failure (Edit before Read, wrong shell syntax)
- an edit to a skill's text, or a fix to a script the user owns (a `/sync` that keeps hitting conflicts)

`ask` findings labelled `permission` are turns where Claude asked for a go-ahead it didn't need. The fix is a rule that stops the asking, not a faster way to say yes.

## 5. Ask once

Make one `AskUserQuestion` call with one question per finding. In each question, give the finding in a few words with its count, and list each fix as an option whose label says what "yes" does, for example "Add a `gauth` function to your PowerShell profile" or "Append the Edit-before-Read line to ~/.claude/CLAUDE.md". Every question also gets the option `Leave it`.

## 6. Apply

- For each picked fix, make exactly that change and nothing else.
- For each `Leave it`, run `repeat-o-scope decline <id>` so it isn't suggested again.
- Report what changed as one short list: file and change.
- If more findings are left and the user approved at least one fix, ask once whether to do the next batch. Otherwise end with the list. Don't ask "want me to…?" about anything already picked.

## Never

- Push, commit, post, or file issues.
- Change a file the picked option didn't name.
- Send findings anywhere: they quote the user's own prompts.
