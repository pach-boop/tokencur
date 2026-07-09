# Changelog

All notable changes to tokencur are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) · Versioning: [SemVer](https://semver.org).

## [Unreleased]

### Added

- Codex CLI ingester: parses rollout `token_count` events, splitting
  OpenAI's cached input out of `input_tokens` (no write premium).
- Kimi Code ingester: parses per-turn `usage.record` wire-log lines.
- Multi-source report: with no arguments, every known local source is
  scanned (Claude Code, Codex, Kimi Code) with per-source totals.
- Vendor prefixes (`moonshot-ai/…`) are stripped when resolving rates.
- FOCUS 1.2 normalizer and `python -m tokencur.export`: one charge row
  per token bucket, showback cost semantics, explicit-null InvoiceId.
- CI conformance gate: the export must pass the FinOps Foundation's
  own `focus-validator` (spec 1.2) on every push.
- Documented proxy rate for Kimi Code's `kimi-k2.7-code-highspeed`
  alias (kimi-k2.6 list rates), so real usage no longer reads as
  unpriced.

- Vendored snapshot of the LiteLLM community price database as a
  fallback pricing layer (284 models: Anthropic, OpenAI, Gemini,
  DeepSeek, Kimi/Moonshot, GLM/Z.ai, Ollama), with a deliberate
  refresh script. The curated card still wins; cache rates come from
  explicit per-model fields since multipliers differ across providers.

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
