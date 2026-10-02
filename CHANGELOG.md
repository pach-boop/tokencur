# Changelog

All notable changes to tokencur are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) · Versioning: [SemVer](https://semver.org).

## [Unreleased]

### Added

- Project governance: `CONTRIBUTING.md` (ground rules, how to add a
  source), `SECURITY.md` (what tokencur reads and writes, private
  vulnerability reporting), a Contributor Covenant code of conduct,
  issue and pull request templates and `CODEOWNERS`. Dependabot
  (Actions and Python extras), Dependabot security updates, private
  vulnerability reporting, CodeQL code scanning and an OpenSSF
  Scorecard workflow are enabled. Workflows are pinned to commit SHAs
  and run with least-privilege permissions, timeouts and
  cancel-in-progress concurrency; `ci` runs on pull requests and on
  pushes to `main`.
- Ledger: every command — `report`, `export`, `recommend`, the
  observatory and the dashboard — now keeps what it scans in a local
  SQLite ledger and reports the ledger's full history, so totals no
  longer shrink when coding agents delete old logs (Claude Code removes
  transcripts after `cleanupPeriodDays`, 30 by default). Usage events
  are deduplicated on a stable `record_id` built from raw source
  fields; events whose logs are gone keep their last known values.
  Metadata only, owner-readable file, schema-versioned, stdlib
  `sqlite3` (still no runtime dependencies). Location:
  `$TOKENCUR_LEDGER`, else `$XDG_DATA_HOME/tokencur/ledger.sqlite3`.
- Observatory history gaps: `subscriptions.json` can declare
  `history_gaps` (usage that happened but whose logs were lost before
  the ledger existed). The page discloses each gap in a note at the
  top, and the affected subscription fee is not counted across it, so
  lost records don't depress subscription leverage. Fees still count
  over the whole window everywhere else — a paid month with no usage is
  real money.

- Price card page: `python -m tokencur.prices` renders `docs/prices/`
  (published via GitHub Pages) — the curated Anthropic rates, the
  community-snapshot coverage count, and a dated change log built from
  the git history of the snapshot file. The daily price-watch action
  commits only on a real rate move, so the timeline is that action's
  public face. It distinguishes introduction / expansion (models added)
  / rate move; the pure `diff_models` carries the test weight.

- Observatory: `python -m tokencur.observatory` renders the FOCUS
  dataset as a self-contained static dashboard (`docs/observatory/`,
  published via GitHub Pages) — KPIs, daily cost by service, cost by
  model, token-type mix and measured savings. Aggregates only:
  workspace names, session ids and content never enter the snapshot,
  enforced by test.
- Subscription-aware money concepts: `subscriptions.json` declares the
  flat fees actually paid; the observatory separates real outlay,
  showback usage value and counterfactuals, and leads with
  **subscription leverage** (usage value ÷ outlay over the same window).

### Changed

- The curated Anthropic card mirrors Anthropic's pricing page as of
  2026-10-02 (was 2026-07-06): adds Claude Opus 5.5, Fable 5.1,
  Mythos 5 and 5.1, Opus 5, Sonnet 5.5 and the retired Opus 4,
  Sonnet 4 and Haiku 3.5, keyed so their dated ids resolve. Cache
  reads follow each model's published multiplier: 0.05x on Opus 5.5,
  0.025x on Fable 5.1 and Mythos 5.1, 0.1x elsewhere. Opus 5.5 was
  the maintainer's largest Claude cost and was priced only by the
  community snapshot; the values are unchanged, now from the source.
- `UsageRecord` moved to its own module, `tokencur.records`: pricing,
  the FOCUS normalizer, the ledger and every ingester now depend on a
  shared type instead of on the Claude Code ingester. The old import
  path `tokencur.ingest.claude_code.UsageRecord` still works (same
  class, pinned by a test).
- Honest labeling throughout: the report prints `API-EQUIVALENT TOTAL
  (showback)`, and the observatory/dashboard say "usage value", "avoided
  by provider caching (counterfactual)" and "right-sizing headroom" —
  none of these are money spent or saved under flat subscriptions.
- The recommend CLI speaks the same language: `AVOIDED` / `HEADROOM`
  section headers, `Total what-if headroom`, and a closing
  flat-subscription caveat (was "measured savings" / "Total potential
  savings").
- `DEFAULT_SOURCES` moved from `tokencur.report` to `tokencur.sources`,
  whose `load_records()` is now the single loader behind every command
  (five copies of the scan loop removed).
- The version has one source, `tokencur.__version__`, which
  `pyproject.toml` reads dynamically (`__init__` had drifted to 0.1.0
  while the package shipped 0.2.0).
- CI conformance gate pinned to `focus-validator` 2.2.1 (same result:
  136 rules pass).
- README states what is ingested today (three coding agents) instead
  of implying Gemini or local-model ingestion, adds an architecture
  diagram, updates pricing coverage (290+ live models, daily refresh,
  retired models kept) and explains why the target is FOCUS 1.2.

### Fixed

- Claude Sonnet 5 was valued at $3/$15 per MTok. Its $2/$10 launch
  price, announced as introductory through 2026-08-31, became the
  standard price and the rise to $3/$15 was cancelled. The community
  snapshot already said $2/$10; a new test now fails whenever the
  curated card and the snapshot disagree on a model both price.
- The report's table sized its columns by hand, so billion-token
  cache totals and long model ids ran into the next column. Columns
  now fit their widest cell. Rows are counted as "model calls"
  (was "assistant messages", which only fit Claude Code).
- **Codex usage was counted about twice.** Codex re-sends each
  `token_count` report under a new timestamp (alongside rate-limit
  updates), and tokencur counted every one. A call now counts only
  when the session's running total moves; reports with no billable
  tokens are skipped. On the maintainer's logs Codex drops from 16,130
  to 8,031 calls and from $685.66 to $332.95 of API-equivalent value,
  and all 94 rollouts now reconcile exactly with Codex's own running
  total (a new test pins that reconciliation). Ledger schema 2 retires
  the re-sends already stored: the upgrade backs the file up first,
  and retired rows move to a `superseded` table with the reason, never
  deleted. Every published figure that included Codex overstated it.
- Undated usage is never given a date. A record with no timestamp
  was exported to FOCUS on 1970-01-01, which would also stretch the
  observatory's daily chart back to 1970, and a malformed timestamp
  crashed the whole export. Both are now skipped by the FOCUS
  normalizer and counted on stderr, like unpriced usage; the report
  keeps their cost in the total under an `undated` day.
- `recommendations()` walked its input twice, so a generator (any
  one-shot iterable) silently lost every right-sizing result. It now
  materializes the records once; a regression test feeds it a
  generator.
- Pricing snapshot no longer loses models that LiteLLM prunes upstream.
  Retired models keep their last known rate, flagged
  `retired_upstream`, so historical usage stays priced; 56 models
  dropped by earlier daily refreshes were restored from git history.
  Before this, `main` had two failing tests while the badge stayed
  green: the price-watch bot pushes with `GITHUB_TOKEN`, which does not
  trigger CI. The price-watch job now runs the test suite before it
  commits.
- Claude Code's synthetic placeholder messages (model `<synthetic>`,
  all-zero usage — client-side stubs for API errors and interrupted
  turns) are skipped at parse time. They are not API traffic and were
  surfacing as noise in the unpriced-usage section of reports.

## [0.2.0] - 2026-07-13

### Added

- Codex CLI ingester: parses rollout `token_count` events, splitting
  OpenAI's cached input out of `input_tokens` (no write premium).
- Kimi Code ingester: parses per-turn `usage.record` wire-log lines.
- Multi-source report: with no arguments, every known local source is
  scanned (Claude Code, Codex, Kimi Code) with per-source totals.
- Vendor prefixes (`moonshot-ai/…`) are stripped when resolving rates.
- Vendored snapshot of the LiteLLM community price database as a
  fallback pricing layer (284 models: Anthropic, OpenAI, Gemini,
  DeepSeek, Kimi/Moonshot, GLM/Z.ai, Ollama), with a deliberate
  refresh script. The curated card still wins; cache rates come from
  explicit per-model fields since multipliers differ across providers.
- FOCUS 1.2 normalizer and `python -m tokencur.export`: one charge row
  per token bucket, showback cost semantics, explicit-null InvoiceId.
- CI conformance gate: the export must pass the FinOps Foundation's
  own `focus-validator` (spec 1.2) on every push.
- Documented proxy rate for Kimi Code's `kimi-k2.7-code-highspeed`
  alias (kimi-k2.6 list rates), so real usage no longer reads as
  unpriced.
- Cross-tests against the FinOps Foundation's official sample data
  (CC BY 4.0 fixture slice): shared core vocabulary, mutually
  parseable datetimes (ours strict ISO-8601 Z), spec-bounded
  ChargeCategory, and cost/currency conventions.
- Daily price-watch workflow: refreshes the LiteLLM snapshot and
  commits only on real rate changes.
- Streamlit + DuckDB dashboard (`pip install -e .[dashboard]`): KPI
  tiles, daily cost by service, cost by model, token-type mix and
  unit economics — every view exposing its SQL and table.
- RunPod billing-API probe script (auth recipe + dailyCharges shape),
  groundwork for the billed-cost ingester.
- Recommendation engine (`python -m tokencur recommend` and a
  dashboard section): measured caching ROI per model (savings vs
  re-sending cached tokens as fresh input) and what-if model
  right-sizing over curated one-tier-down pairs, emitted only when
  the sibling is actually cheaper at list rates.
- `python -m tokencur <report|export|recommend>` command router.

## [0.1.0] - 2026-07-06

### Added

- Claude Code JSONL ingestion: one usage record per assistant message,
  deduplicated across streamed lines and resumed sessions. Reads usage
  metadata only — never conversation content.
- Versioned Anthropic rate card at public list prices, with cache-tier
  economics (reads 0.1x input; writes 1.25x for 5m TTL, 2x for 1h TTL).
  Unknown models surface as unpriced usage instead of costing $0.
- `python -m tokencur.report`: per-model and per-day API-equivalent
  list cost over local Claude Code logs. Stdlib only.
