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

## Decided

- The settings audit is `repeat-o-scope doctor`, not a fifth report.
- The gist lives in the repo as `docs/why.md`, and the README links it near the top.

## Out of scope

Hosted dashboard, team rollup, billing.
