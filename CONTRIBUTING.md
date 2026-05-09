# Contributing

Bug reports and pull requests are welcome.

## Local setup

```bash
# Python (ML stack)
pip install -r ml/requirements.txt
pip install pytest

# Node (scraper)
cd scraper && npm ci
```

## Running tests

```bash
# Python tests (56 tests)
python -m pytest tests/ -v

# JS validation tests (9 tests, no browser needed)
node --test tests/test_scrape.js

# Playwright fixture test (requires scraper deps)
cd scraper && node --test test_scrape.js
```

All three suites must pass before a PR is considered.

## Running the scraper locally

```bash
cd scraper
node scrape.js               # prints JSON to stdout, stderr shows rates + ratio checks
node scrape.js | node update-and-notify.js   # appends to data/prices.json
```

The scraper validates karat ratios on extraction. If Tanishq changes their page structure, the scraper will throw and dump the page body to stderr for diagnosis.

## Running the ML pipeline locally

```bash
python ml/forecast.py        # writes data/forecast.json
python ml/backtest.py        # writes data/backtest.json

GROQ_API_KEY=<your-key> python ml/commentary.py   # writes data/commentary.json
```

## Philosophy

- **No model artefacts in the repo.** LightGBM retrains from scratch in < 1 second on this data volume. Committing a model pkl would make the repo opaque and create version drift.
- **Degrade gracefully.** Forecast, backtest, and commentary are all `continue-on-error: true` in CI. The scrape (prices.json update) is the only hard requirement.
- **Honest metrics.** The naive baseline is tracked alongside the model. If the model doesn't beat it, that's reported accurately — see `data/backtest.json` and the Model performance section in the PWA.
- **No buy/sell advice.** The LLM commentary system prompt explicitly prohibits investment recommendations. Don't add any.

## Reporting bugs

Open a GitHub issue with:
1. What you expected to happen
2. What actually happened (include Action run URL if it's a CI failure)
3. Steps to reproduce

For security issues, see [SECURITY.md](SECURITY.md).
