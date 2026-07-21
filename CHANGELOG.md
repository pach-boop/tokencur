# Changelog

All notable changes to tokencur are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) · Versioning: [SemVer](https://semver.org).

## [Unreleased]

### Added

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

- Honest labeling throughout: the report prints `API-EQUIVALENT TOTAL
  (showback)`, and the observatory/dashboard say "usage value", "avoided
  by provider caching (counterfactual)" and "right-sizing headroom" —
  none of these are money spent or saved under flat subscriptions.
- The recommend CLI speaks the same language: `AVOIDED` / `HEADROOM`
  section headers, `Total what-if headroom`, and a closing
  flat-subscription caveat (was "measured savings" / "Total potential
  savings").

### Fixed

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
