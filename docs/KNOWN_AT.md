# Adding a data source: its `known_at` comes first

Every input an evaluation uses must say **when it became known**: the UTC instant it was
published or captured, not its value date. The evaluation harnesses refuse to score a prediction
that used an input known at or after the prediction moment (`ml/leak_guard.py`, ADR 061).

## Checklist for a new source

1. **Find the clock.** When is a value dated D actually public? Write down the evidence: exchange
   hours, a measured comparison against intraday bars (ADR 058 did this for GC=F and GLD), or the
   capture time if this repo scrapes it. If you cannot pin it down, pick the **latest** plausible
   time (INR=X uses 23:59 UTC of D). A late clock can block a legitimate input. An early clock lets
   a leak through.
2. **Add it to `ml/known_at.py`, and only there.** Add a `Clock` (daily series) or a function
   (captured or intraday series), plus one docstring line that marks it VERIFIED or ASSUMED. A new
   `ml/macro.py` ticker needs an entry in `MACRO_DAILY_CLOCKS`, or
   `tests/test_leak_guard.py::test_every_macro_series_has_a_clock` fails.
3. **Store what you need to compute it.** Keep the value date (`<col>_asof_date`) and, for
   anything fetched or scraped, the fetch or capture timestamp with a time zone (`...Z` or an
   offset). A naive timestamp is rejected, never assumed to be UTC.
4. **Declare the prediction moment where you score.** Use the latest instant a forecast could be
   issued and still count as the claim you publish. Examples: "as of IST day D" is
   `ist_day_end(D)`; "a nowcast of the reading at T" is T; "before day t's move starts" is
   `ist_day_start(t)`.
5. **Build `KnownInput`s and check them.** Use `assert_known_before` or a `LeakGuard(mode="raise")`.
   Use `filter_known_before` if the harness should drop late inputs instead of refusing.
   `mode="report"` is only for a registered analysis whose published number would change. In that
   case the report is a finding: write it down (ADR 061 F1-F3) instead of fixing it silently.
6. **Add a boundary test.** An input known exactly at the moment is blocked. One second earlier
   is allowed.

## Existing conventions (full detail in the `ml/known_at.py` docstring)

| Source | known_at |
|---|---|
| IBJA AM / PM, value date D | 12:00 / 17:00 IST on D (assumed), or the row's `fetched_at` if later |
| Retailers (prices.json, fusion snapshots) | capture timestamp |
| COMEX GC=F daily, dated D | 13:30 America/New_York on D (settlement, verified ADR 058) |
| USD/INR INR=X daily, dated D | 23:59 UTC on D (conservative, no exact clock) |
| Any hourly bar | bar start + 1 h |
| Other macro dailies | exchange close on D (assumed; table in the docstring) |
| Feature-store row | live: capture (macro, Tanishq); max(capture, publish) for IBJA. Backfill: the source clock on `_asof_date` |
