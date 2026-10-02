# gold-rate-tracker: project instructions for Claude Code sessions

Claude Code loads this file automatically in every session in this repo. It lives in `.claude/`
so the GitHub Pages build (Jekyll skips dot-directories) never publishes it.

## Reporting standard (GG, 2026-09-25): mandatory for every milestone and final report

GG found earlier reports too compressed. Every milestone report and every final report uses this
format. **Do not compress.** Do not drop an item because nothing changed: say "no change" and why.

1. **SUMMARY**: at most 5 lines covering what changed, what is live, and what needs GG.
2. **PER ITEM, IN FULL**: one section per task in the brief, each with:
   - **Asked**: what the brief required.
   - **Done**: what you actually did, step by step.
   - **Evidence**: PR numbers, commit SHAs, workflow run IDs, file paths.
   - **Numbers**: every result with n, effective n, interval and p-value, and the baseline it was
     compared to. Mark each number VERIFIED (you ran it) or INFERRED (you reasoned it). Never state a
     metric you did not measure.
   - **What changed for users**: live, flagged (and whether the flag is off), or nothing. Be explicit.
   - **Risks**: what could go wrong, and how it would show up.
   - **Remaining**: what is not done, and why.
3. **SURPRISES AND MISTAKES**: anything unexpected, including your own errors and any premise in
   the brief that turned out to be wrong.
4. **WAITING ON GG**: one numbered list containing only READY items. Each has exact steps, links, and
   the order to do them in. READY means every required check green, no conflicts against current
   master, a per-spec-item table in the PR body, and `scripts/check_required_checks_positive.py`
   passing. Re-check readiness immediately before reporting. Never report complete on green CI alone.
