# audit: measure, judge, themes and a plan in one pass

2026-11-17. Status: draft. Follows `2026-11-17-budget-audit-design.md`; replaces its separate `doctor`.

## Problem

The pieces don't connect. `measure` knows what the waste cost and not where it came from. `prompts`,
`commands`, `asks` and `fails` know where it came from and only count it. The skill reads the four reports
and never `measure`, so fixes get ranked by how often something happens, not by how much usage it burns.

## The pass

`/repeat-o-scope` is the only entry point:

1. **Measure + evidence**: `repeat-o-scope audit --format json` scans the transcripts once.
   `waste.measure` already charges each wasted call to one kind; it also keeps that call's evidence
   (error line, file path, closing line). `audit` groups the evidence per kind, as `fails` and `asks` do,
   and every group carries its own turns, tokens and share of usage.
2. **Settings**: `audit` also reads `~/.claude/settings.json` and `CLAUDE.md` against the `docs/why.md`
   checklist: effort level, `skillOverrides`, enabled skills, status line, scope and read-once rules,
   `CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS`, no `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=25` on 200k models. These
   are what turn expired-cache and past-half-context waste, which has no text to group, into fixes.
   `audit` calls the checks of `repeat-o-scope doctor` (branch `doctor`, PR #13), not a copy of them.
   Skills with `disable-model-invocation: true` run only as `/name` and cost nothing per turn, so they
   don't count as enabled. To cut the count, the plan offers two kinds of fix:
   - Make one of the user's own skills in `~/.claude/skills` a `/command` with that frontmatter line.
   - Switch off a plugin skill (`superpowers:*`, `egm:*`, ...) with `skillOverrides` or `enabledPlugins`.
     An edit in the plugin cache is lost on update.
3. **Judge** (one example per group): is it real or noise, and for asks, which kind. It uses the first
   judge that is available, in this order:
   1. Jev (`TYPESAFE_API_KEY`, or `TYPESAFE_AI_API_KEY` as vainu-data names it), in `audit`.
   2. Gemini Decision 1 (`gemini-decision-1`, Gemini Decisions API, `GEMINI_API_KEY`), in `audit`.
   3. Clef, in `audit`: Workers AI,
      `POST https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/run/@cf/cloudflare/clef`
      with `Bearer {CLOUDFLARE_API_KEY}`. The body is Jev's question format
      (`{model, state, questions: {name: {type: choice|noul, instructions, criteria}}}`); answers come back
      in `result.answers` with `choice`, `confidence` and `noul`, but no probabilities. Port of
      `ClefClient` in vainu-data `python/libraries/financial_chunking/tools/sections.py`.
   4. Haiku, in the skill: no API key. When `audit` reports the groups unjudged, the skill spawns a
      subagent with `model: haiku` that answers the same questions over the JSON.

   A judge is available when its env var is set, its client library imports, and a simple test call
   returns a non-error status. The judge used is in the output.
4. **Themes** (Claude, in the skill): merge groups across kinds that share one root cause, for example
   "Windows shell confusion" = denied Bash + failed quoting + retries.
5. **Plan** (Claude, in the skill): at most 4 fixes, ranked by the share of usage each would save, each a
   concrete edit (file + exact text). One `AskUserQuestion`, apply the picks, decline the rest.
6. **Baseline**: `audit` writes its measurement to the user data dir, for a later `compare`.

`prompts` and `commands` stay in the audit as count-only findings: they are about automating, not waste.
The old subcommands stay for debugging.

## Open

- Gemini Decision 1: the `google-genai` `client.decisions.create` shape, checked against Google's docs,
  not secondary write-ups.
## Out of scope

`compare` (only the baseline is written now), a hosted dashboard, team rollups.
