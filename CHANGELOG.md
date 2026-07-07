# Changelog

All notable changes to tokencur are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) · Versioning: [SemVer](https://semver.org).

## [Unreleased]

### Added

- Vendored snapshot of the LiteLLM community price database as a
  fallback pricing layer (212 Anthropic/OpenAI/Gemini models), with a
  deliberate refresh script. The curated card still wins; cache rates
  come from explicit per-model fields since multipliers differ across
  providers.

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
