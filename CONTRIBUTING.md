# Contributing to tokencur

Thanks for considering it. tokencur is small on purpose; these notes keep
it that way.

## Ground rules

1. **Metadata only.** Ingesters read token counts, model ids, timestamps,
   workspace and session ids. They never read, store or log message
   content. A change that does is declined.
2. **Unpriced is never $0.** A model with no rate surfaces as *unpriced
   usage*. Silently valuing it at zero is the one bug this project exists
   to avoid.
3. **No runtime dependencies.** The core is standard library only
   (`sqlite3`, `json`, `csv`, `pathlib`). Optional extras such as
   `[dashboard]` may depend on more; the core may not.
4. **Explainable over clever.** Nothing is merged that the maintainer does
   not fully understand and can defend. If you used an AI assistant, say
   so in the PR; the policy applies to the person submitting, not the tool.
5. **Money words mean one thing each.** *Outlay* is money paid. *Usage
   value* is API-equivalent list cost (showback). *Avoided* and *headroom*
   are counterfactuals. Keep them apart in code, docs and output (see the
   README's "Money concepts").

Decisions behind these rules are recorded in [docs/adr](docs/adr/README.md).
A change that reverses one adds a new record that supersedes it.

## Set up

Python 3.11 or newer.

```bash
git clone https://github.com/pach-boop/tokencur
cd tokencur
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```

Lint and format with the pinned ruff (CI runs the same two commands), and
optionally let pre-commit run them before every commit:

```bash
ruff check . && ruff format --check .
pip install pre-commit && pre-commit install   # optional
```

CI also measures branch coverage with every optional extra installed and
fails under 90%, runs the suite on Linux, macOS and Windows, and builds
and installs the wheel. Leaked file or database handles fail the suite.

The FOCUS conformance gate runs the FinOps Foundation's own validator
(Python 3.12+):

```bash
pip install focus-validator==2.2.1
python scripts/validate_focus.py
```

## Adding a usage source

One module per source under `src/tokencur/ingest/`, exposing
`iter_usage_records(root: Path) -> Iterator[UsageRecord]`.
[`codex.py`](src/tokencur/ingest/codex.py) is a compact example.

- Yield one `UsageRecord` per billable call. Map the source's token fields
  onto input / output / cache read / cache write (5m, 1h); write zeros for
  dimensions the provider does not bill, and say so in the module docstring.
- Give every record a stable `record_id` built from the source's own
  identity fields: a request id when the source logs one, otherwise
  session + timestamp + a fingerprint of the raw usage object
  ([`ingest/identity.py`](src/tokencur/ingest/identity.py)). The ledger
  deduplicates on `(source, record_id)`, so two scans of the same logs
  must produce the same ids.
- Skip malformed lines. Never raise on one bad line.
- Register the default log location in `sources.DEFAULT_SOURCES` and the
  provider and service names in `focus.py`.
- Tests: a synthetic fixture written inline (as `tests/test_codex.py`
  does) covering the happy path, a skipped line and id stability.
- Pricing: if the models are not in the LiteLLM snapshot, add a documented
  rate to `pricing.RATE_CARD` with its public source and date.

## Changing prices

The curated card in [`pricing.py`](src/tokencur/pricing.py) wins over the
LiteLLM snapshot. Edit it only with a public source and a date in the
comment. The snapshot is refreshed by the price-watch action; do not
hand-edit `litellm_snapshot.json`, run `scripts/update_pricing_snapshot.py`.

## Pull requests

- Branch from `main`. `main` only moves through pull requests with green
  CI; the daily price-watch bot is the one exception.
- Commits follow [Conventional Commits](https://www.conventionalcommits.org):
  `feat:`, `fix:`, `docs:`, `test:`, `chore(prices):`...
- Add a line under `[Unreleased]` in `CHANGELOG.md`
  ([Keep a Changelog](https://keepachangelog.com)).
- Fixtures are synthetic or redacted to metadata. No real logs.

## Releasing (maintainer)

1. In `CHANGELOG.md`, move `[Unreleased]` under `[X.Y.Z] - YYYY-MM-DD`, and set
   `__version__` in `src/tokencur/__init__.py` to `X.Y.Z`. Merge that through a
   pull request.
2. Tag the merged commit and push the tag:

   ```bash
   git tag vX.Y.Z && git push origin vX.Y.Z
   ```

3. The release workflow checks that the tag matches `__version__`, builds the
   sdist and wheel, attests their build provenance and drafts a GitHub release
   with them. Write the notes from the changelog, then publish the draft.
4. PyPI, once: on pypi.org add a trusted publisher (project `tokencur`, owner
   `pach-boop`, repository `tokencur`, workflow `release.yml`, environment
   `pypi`), then set the repository variable `PUBLISH_TO_PYPI` to `true`. From
   then on, each tag also publishes to PyPI, with no token stored anywhere.

## Reporting a vulnerability

See [SECURITY.md](SECURITY.md). Not in a public issue.
