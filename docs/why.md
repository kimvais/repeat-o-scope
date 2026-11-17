# How my Claude Code set-up stays within budget

2026-10-25

## The short answer

Most tokens go on wasted turns rather than on the work itself: a wrong guess, a retry, a re-read, a scope creep that then needs testing and reverting. My set-up is built to cut those turns. It does not use a cheaper model.

- **Fewer turns per task.** The instructions make Claude read everything once, edit once and test once, and stay inside the request.
- **Less context per turn.** Unused skills are switched off, MCP tools load only on demand, and long output goes to a file instead of the chat.
- **No rediscovery.** Every gotcha I hit goes into memory once, so the next session does not pay to learn it again.
- **I can see the meter.** The status line shows context use and the 5-hour limit, so I start a fresh session before a bloated one gets expensive.

## Instructions that matter

My global `~/.claude/CLAUDE.md` is about 6 KB. These are the rules that save the most turns:

| Rule | What it prevents |
| --- | --- |
| **The issue is the whole scope.** Repo rules shape the code but never add work; anything extra goes in the summary as "left out (OOS)". | Claude "improving" nearby code, which then needs review, tests and a revert. |
| **No read, edit, read, edit loop.** List every file needed, read them in one pass, make all edits, run the tests once. | The most common token sink: one file at a time, re-reading after each edit. |
| **Read with the Read tool before editing**, never `cat` or `Get-Content`. | Refused edits. I counted 84 of them in old transcripts before adding this. |
| **Multi-line scripts go in a file**, never inline `python -c` or heredocs. | Quoting errors and retries, especially on Windows. |
| **Keep going.** Run local, reversible, in-scope steps without asking. Ask only for real choices, outward-facing actions or money. | Round trips where Claude stops to ask "shall I run the tests?" |
| **If I question an addition and it isn't needed, delete it now.** | A debate that costs more than the line it is about. |
| **Platform facts up front:** Windows, PowerShell, `uv`, which tools exist. | Claude trying bash, `pip` or `jq` first, failing, then retrying. |

The repo's `CLAUDE.md` adds the team's coding rules, with "every line of code is a liability" as the first one. Less code means less to read, test and fix.

## Essential skills

A skill turns a multi-step job into one known recipe, so Claude does not work it out again each time. These are the ones I'd keep:

- **A "check" skill**: format, lint and tests for only the changed packages, in one command. Claude never assembles the test command itself or runs the whole monorepo.
- **A code-search skill**: searches code, merge requests, issues and earlier attempts across every project through an MCP server. A single query replaces a dozen grep and read calls, and it finds work that already exists before Claude writes it again.
- **`/ship` and `/sync`**: commit, rebase, push, draft MR and watch the pipeline. Without them that's ten-plus turns of git every time.
- **`vjuu`**: anything longer than 20 lines opens in a review window instead of the chat. I comment on lines and the comments come back as input. This keeps chat output short and my feedback precise.
- **`i-have-adhd`**: an output style that leads with the next action and leaves out tangents. It's meant for me, but shorter answers also mean fewer tokens.
- **`superpowers`** (brainstorming, writing-plans, systematic-debugging): agreeing a plan up front costs a little and saves big rewrites. Leave it out for small tasks; it is not free.

**Turn off what you don't use.** Every enabled skill's description sits in context on every turn. I switch off six through `skillOverrides` in `settings.json`.

## Key configuration

The biggest lever is **effort**: Opus at `medium`, not `high`. Good instructions make extra thinking mostly unnecessary.

| Setting | Value | Why |
| --- | --- | --- |
| `model` + `modelSettings.effortLevel` | `opus`, `medium` | A strong model gets it right first time; medium effort cuts thinking tokens on routine work. |
| `skillOverrides` | 6 skills `off` | Less fixed context on every turn. |
| MCP servers | ~15 connected, loaded on demand | Tool schemas stay out of context until a task needs them, so connectors cost almost nothing while idle. |
| `permissions.defaultMode` | `auto`, with `autoMode.allow` rules and an `environment` description | Claude knows what is safe here, so it doesn't stop to ask or pick a timid detour. |
| `permissions.deny` | `Bash` (Windows) | Stops failed bash attempts before they happen. |
| `statusLine` | Script showing context % and 5-hour limit % | I see a session getting heavy and start fresh. |
| `SessionStart` hook | Checks cloud credentials | Claude asks me to log in up front, not halfway through a job. |
| `PreToolUse` hook | Before the first test run, shows the diff against `main` once | Claude reverts out-of-scope files before tests run on them. |
| Memory | ~70 one-fact files, indexed in `MEMORY.md` | Each gotcha (stale tool, wrong database, broken flag) is learned once. |

Habits matter as much as settings. Use one session per task, `/clear` between tasks, and subagents only for genuinely parallel work: each one starts from zero context and re-reads what you already know.

## If you copy only three things

1. **Add the scope rule and the "read once, edit once, test once" rule** to your `~/.claude/CLAUDE.md`, word for word from the table above.
2. **Set effort to medium** in `/config`, and turn off skills you never use.
3. **Add context % and usage % to your status line**, and start a new session when context passes about half.

After that, every time Claude gets something wrong twice, add one line to `CLAUDE.md` or memory so it doesn't happen a third time. That habit adds up more than any single setting.
