# Findings skill: from reports to fixes

Status: draft, waiting for Kimmo's approval. Closes the open question in #5.

## Intent

repeat-o-scope's four reports (`prompts`, `commands`, `asks`, `fails`) find what keeps repeating in Claude Code sessions: `gcloud auth login` typed 24 times a month, 152 "say ship and I'll push" endings, 47 Edit-before-Read failures. Today they stop at a markdown list that a person has to read and act on. That last step is the boring part the tool exists to remove.

Kimmo's rule: **the output of every report must be something an accompanying skill can work on.** The skill turns findings into concrete fixes, and the user approves each one.

The audience is any Claude Code user, with Kimmo's colleagues first. So the skill ships with the repo as a plugin and works on a machine set up differently from Kimmo's.

## Goals

1. Every report can emit machine-readable findings (`--format json`). The markdown stays as the human view and remains the default.
2. A `repeat-o-scope` skill, shipped inside the PyPI package, runs the reports, takes the top findings, and drafts a **concrete** fix for each one.
3. The user approves or declines each fix through choices that spell out what "yes" does. Only approved fixes are applied.

## Non-goals

- Scheduled or automatic runs (a weekly job). The skill runs when the user invokes it.
- The transcript archive (#2).
- Jev for `prompts` and `commands`.
- Measuring whether a fix worked. The next run's 30-day window shows that by itself.

## Anti-goals (failure even if it technically works)

- **Generic advice.** "Consider automating your login" is a failure. A finding gets a specific change (the exact function text, the exact hook entry, the exact CLAUDE.md line) or an explicit "leave it".
- **Applying anything without a pick.** No file outside the user's approval changes, and nothing is pushed, posted or filed.
- **Leaking transcripts.** Findings hold redacted prompts, commands and errors. They never leave the machine except into the Claude session running the skill, and into TypeSafe when the user passes `--jev`.
- Asking twice. When the user has picked, the skill applies the fix and reports what changed. It doesn't end with "want me to…?" (see vjuu#87, vjuu#92).

## Decided (Kimmo)

- The reports produce findings, and a skill works on them. Applies to all four reports, not just `commands`.
- Fixes are drafted by Claude itself (the model running the skill), not by Jev. Jev stays a labeller: the kind of ask in `asks`, real failure versus noise in `fails`.
- Fixes are offered as choices whose labels say what "yes" does, for example "Add a `gauth` function to your PowerShell profile" or "Append this line to `~/.claude/CLAUDE.md`".
- The tool and the skill ship for any Claude Code user, **through PyPI**: `uv tool install repeat-o-scope`, then `repeat-o-scope install`.
- The repo becomes public later. Nothing here depends on it, since PyPI distribution works with a private repo.

## Decided (Claude's calls, open to veto)

1. **`--format json` on all four reports**, written to stdout. One document per report:
   - `report` (`prompts` | `commands` | `asks` | `fails`), `days`, `generated_at`, `jev` (bool)
   - `totals`: items read, groups, one-offs
   - `findings`: ranked list, each with
     - `id`: stable across runs, from the report name and the normalised top variant (uuid5)
     - `kind`: `prompt` | `command` | `ask` | `failure` | `denial`
     - `total`, `variants` (normalised text and count, most frequent first)
     - `examples`: up to 3 raw, redacted originals (the prompt as typed; for failures, tool, attempt and error)
     - `label`: Jev's label when `--jev` was used (`permission`, `choice`, …, or `real`), else null
   - `id` only has to be stable for identical data. Cross-run memory uses variants (decision 3).
   - `examples` are the redacted originals behind the most frequent variant, which means carrying the originals through clustering. Redaction removes credentials only. Paths, names and internal URLs stay, which is acceptable because findings never leave the machine except into the user's own Claude session.
   - For `asks`, a finding is one kind of ask (with `--jev`) or the whole set of wasted endings (without), and its examples are the most frequent closing lines.
   - For `fails`, failures and denials are findings in the same list, told apart by `kind`. `totals` counts them separately. `--jev` drops noise groups from failures only.
2. **Approval goes through `AskUserQuestion`**, one question per finding with its fix options plus "Leave it". With vjuu installed, its hook already moves these into vjuu, and without it they still work in the terminal. This keeps vjuu optional for colleagues.
3. **Declined findings are remembered, by the CLI** (so it is tested code, not skill prose). `repeat-o-scope decline <id>` stores the finding's variants in `platformdirs.user_data_path('repeat-o-scope', appauthor=False)/declined.json` (`platformdirs` is already a dependency). The reports leave out any finding whose top variant is in a declined set, and `--all` shows them again. When the user says "show everything", the skill passes `--all`. Matching on variants rather than on `id` survives the window sliding, when a group's top variant or its HDBSCAN boundaries shift.
4. **Fix kinds the skill considers**, so a builder knows the range: a shell function or alias (the user's profile: `$PROFILE`, `~/.zshrc`, `~/.bashrc`), a script, a Claude Code hook in `~/.claude/settings.json`, a permission rule, a line in `~/.claude/CLAUDE.md`, an edit to a skill's text, a fix to a script the user owns, or leave it. The skill picks per finding after reading the relevant files. One finding can get two or three alternative fixes as separate options.
5. **Top findings per run:** at most 4 at a time (`AskUserQuestion`'s limit per call). Take the top finding of each report first, then the rest by `total`, so that one big `asks` finding can't crowd out everything else. The skill asks whether to continue with the next batch only if there are more and the user approved at least one.
6. **Jev in the skill:** the skill passes `--jev` only when `TYPESAFE_API_KEY` is already set in the environment. It never reads secrets itself.

7. **The skill ships inside the Python package, not as a plugin.** It lives at `src/repeat_o_scope/skill/SKILL.md` as package data. `repeat-o-scope install` copies it to `~/.claude/skills/repeat-o-scope/` and registers `repeat-o-scope-worklog` as a Stop hook in `~/.claude/settings.json`. The install is idempotent, leaves other hooks alone, and replaces an existing `worklog.ps1` entry instead of adding a second logger. `repeat-o-scope uninstall` reverses both. A plugin marketplace would need read access to the repo, while PyPI doesn't. The skill is named `repeat-o-scope` (`/repeat-o-scope`).

## Constraints

- Windows (PowerShell 7) and macOS/Linux shells. The fix drafts follow the user's actual shell and profile.
- Python 3.12+, installed with `uv tool install`. The plugin doesn't install Python packages itself. When `repeat-o-scope` isn't on PATH, the skill shows the one install command and stops.
- No new runtime dependencies for the JSON output (stdlib `json`).
- Repo house rules: keyword-only arguments, `_LOGGER`, logging to stderr, tests through the real stack with real-shaped transcripts.

## Left to the builder

- The skill's wording, and how `install` locates and edits `settings.json` safely (keep formatting, back it up first).
- A GitHub Actions release workflow that publishes to PyPI with trusted publishing on a version tag. The first publish waits for Kimmo to set up the trusted publisher on PyPI, which is outward-facing and his step.
- Field names beyond those listed, and the JSON's exact shape for `totals`.
- How the skill finds the user's profile file and Claude config.
- Whether `--format json` shares rendering code with markdown or has its own.

## Done when

- `repeat-o-scope <report> --format json` emits the documents above, and tests cover the contract (negative first: no unredacted secret in any field; a stable `id` across two runs).
- Invoking the skill on Kimmo's machine produces at most 4 concrete fix choices from real findings. An approved one is applied, a declined one is recorded and not offered again.
- `repeat-o-scope install` run twice leaves one Stop hook entry and one skill copy, and `uninstall` restores `settings.json` (tests run against a temporary home).
- The README covers `uv tool install repeat-o-scope` and `repeat-o-scope install`.
