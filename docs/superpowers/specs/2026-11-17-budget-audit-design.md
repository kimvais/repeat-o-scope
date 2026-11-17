# repeat-o-scope as a product: the budget audit

2026-11-17. Status: draft, not agreed.

## Pitch

"Your Claude Code setup, audited: the turns you waste, and the fixes."

The gist "How my Claude Code set-up stays within budget"
(gist 886d47d3b1c9f7673684a5fc1bfba475) explains why. repeat-o-scope runs it on your own transcripts
and proves the savings with a before/after.

## Audience and model

Public OSS on PyPI, donationware (GitHub Sponsors). No SaaS: that only makes sense if it can hook into
claude.ai org accounts and run on team members' transcripts, which isn't possible today.

## The loop

1. **Measure**: count waste per kind, in turns and tokens (transcripts carry `usage`). The headline number is
   "% of your 5-hour limit wasted", because subscription users have no per-token bill. Raw tokens are a
   secondary column, and € shows only with `--price` for API users. Waste kinds:
   - refused edits (Edit before Read)
   - re-reads of the same file within a session
   - failed tool calls and retries (already `fails`)
   - attempts at denied tools (bash on Windows)
   - closing asks that got a bare "yes" (already `asks`)
   - sessions that ran past about 50% of their context
2. **Audit settings** with a separate `repeat-o-scope doctor` command (it reads config, not transcripts) against the gist's checklist: effort level, `skillOverrides`, number of enabled skills,
   status line, scope rule and read-once rule in CLAUDE.md.
3. **Fix**: the `/repeat-o-scope` skill drafts one fix per finding and applies only what the user picks.
   Declined fixes stay declined.
4. **Prove**: save a baseline, then `repeat-o-scope compare` against it after 2 weeks: waste per 100 turns,
   before and after.

## Prior art: inevolin/claude-code-usage-analysis

https://github.com/inevolin/claude-code-usage-analysis measures the same transcripts for cache and
compaction cost, not for wasted turns. Its live tests say cache misses dominate: a small fix on a ~97k
session cost ~1.5× a fresh session inside the cache window and ~9.8× after expiry; `/compact` before a
break ~2.5×, after it ~9.7×. Small samples (n=1–2), Opus 5.5, mostly the 5-minute cache.

What to take:
- **Weights per model and per TTL.** `waste._WEIGHTS` is wrong for current models: cache reads are 0.05×
  input on Opus 5.5 and Sonnet 5.5 (0.025× on Fable 5.1/5.2, 0.1× on older models), and 1-hour cache
  writes are 2×, not 1.25×. Transcripts carry `message.model` and `usage.cache_creation`
  (`ephemeral_5m_input_tokens` / `ephemeral_1h_input_tokens`), so the weight can follow both.
- **A seventh waste kind: cache rebuilds.** The first call after a gap longer than the session's cache TTL
  rewrites the whole context. Charge the rewrite above what a warm call would have cost.
- **`doctor` checks** from its recommendations: `CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS` on subscriptions with
  long breaks, and no `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=25` on 200k-context models.

Not taken: its "hidden usage" from `cost_state.totalCostUSD` (not in interactive transcripts), entrypoint
comparisons (Desktop vs CLI) and the compaction-trigger model, which it says failed validation.

## Decided

- The settings audit is `repeat-o-scope doctor`, not a fifth report.
- The gist lives in the repo as `docs/why.md`, and the README links it near the top.

## Out of scope

Hosted dashboard, team rollup, billing.
