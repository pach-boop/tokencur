# tokencur

[![ci](https://github.com/pach-boop/tokencur/actions/workflows/ci.yml/badge.svg)](https://github.com/pach-boop/tokencur/actions/workflows/ci.yml)

**The CUR for your tokens** — an open-source pipeline that converts multi-provider AI/LLM
usage data (Anthropic, OpenAI, Gemini, coding agents, local models) into
[FOCUS](https://focus.finops.org)-conformant cost datasets, validated in CI by the
FinOps Foundation's own validator. Unit economics and a savings-recommendation engine
are next on the roadmap.

> A FinOps tool that practices FinOps on itself: the first dataset is my own real AI spend.

## Why

- **AI cost management is the #1 skill gap in FinOps** (State of FinOps 2026: 98% of
  organizations now manage AI spend).
- **FOCUS already supports tokens** — the spec has columns for token/credit-based billing,
  with OpenAI usage as an official example. The standard is ready; open implementations
  are not.
- The FinOps Foundation's [`focus_converters`](https://github.com/finopsfoundation/focus_converters)
  covers AWS, GCP, Azure and OCI — **zero AI providers**. tokencur aims to contribute an
  AI-provider converter upstream.

## Related work

| Category | Projects | How tokencur differs |
|---|---|---|
| Dev observability | Langfuse, Helicone, LiteLLM | Per-request tracing; no FOCUS output, no finance vocabulary |
| Coding-agent trackers | ccusage, tokscale, TokenTracker | Dashboards without a financial standard behind them |
| Enterprise platforms | Finout, Vantage, CloudZero | FOCUS-aligned but closed source and enterprise-priced |
| Plumbing | OpenCost OpenAI plugin, focus_converters | k8s-bound / cloud-only; tokencur is standalone, multi-provider, analyst-friendly |

## Design principles

1. **Privacy by construction** — ingestion reads usage metadata only (tokens, models,
   timestamps). Conversation content is never extracted.
2. **Measure existing spend, don't generate spend to measure** — the first data source is
   local Claude Code session logs, which already exist on disk. Budget: ~$0.
3. **List-cost showback** — subscription usage isn't billed per token, so costs are
   computed as *API-equivalent list cost*. Pricing has two layers: a curated, dated
   Anthropic rate card ([`pricing.py`](src/tokencur/pricing.py)) that always wins, and a
   vendored snapshot of the community-maintained
   [LiteLLM price database](https://github.com/BerriAI/litellm) as fallback (284 models
   across Anthropic, OpenAI, Gemini, DeepSeek, Kimi/Moonshot, GLM/Z.ai and Ollama,
   refreshed deliberately via
   [`scripts/update_pricing_snapshot.py`](scripts/update_pricing_snapshot.py)). Unknown
   models surface as *unpriced usage* rather than silently costing $0.
4. **Explainable over clever** — every line that ships is one the maintainer fully
   understands and can defend.

## Quickstart

Requires Python 3.11+. No runtime dependencies.

```bash
pip install -e .
python -m tokencur report             # cost summary in your terminal
python -m tokencur export focus.csv   # FOCUS 1.2 conformant dataset
python -m tokencur recommend          # measured + what-if savings
```

With no arguments it scans every known local source on your machine — **Claude Code**
(`~/.claude/projects`), **Codex CLI** (`~/.codex/sessions`) and **Kimi Code**
(`~/.kimi-code/sessions`) — and prints per-model, per-source and per-day
API-equivalent cost, including provider-correct cache economics.

For the visual version — daily trend, cost by model, token-type mix and unit
economics, each view exposing the DuckDB SQL behind it:

```bash
pip install -e .[dashboard]
streamlit run src/tokencur/dashboard.py
```

Real output over the maintainer's own machine (16k+ messages, 280MB+ of logs, ~1.6s):

```text
model                   msgs       input      output   cache_read  cache_write   cost USD
gpt-5.4                 5713  77,366,776   3,659,955  615,555,712            0     402.21
gpt-5.3-codex           9958  51,780,897   3,961,461  748,238,464            0     277.02
claude-fable-5           226      59,096     467,227   35,337,099    5,559,291     170.48
claude-opus-4-8          141      56,692     501,338    7,880,429    2,859,175      45.35
...
by source:
  codex         $685.66
  claude-code   $215.82
  kimi-code     $2.21

TOTAL: $958.26
unpriced usage (model not in rate card): <synthetic> x7, unknown x1
```

## Roadmap

| Phase | Deliverable | Status |
|---|---|---|
| 1 | Repo, thesis, related work | ✅ |
| 2 | Ingest real usage: local agent logs; Anthropic/OpenAI admin-API exports | 🔨 Claude Code, Codex CLI and Kimi Code done; API exports pending |
| 3 | FOCUS normalizer + CSV export, gated in CI by the [Foundation's own validator](https://github.com/finopsfoundation/focus_validator), cross-checked against [official sample data](https://github.com/FinOps-Open-Cost-and-Usage-Spec/FOCUS-Sample-Data) | ✅ |
| 4 | DuckDB + Streamlit dashboard: trends, top spend, unit economics | ✅ v1 |
| 5 | Recommendation engine: caching ROI (measured) + model right-sizing (what-if) | ✅ v1 — batch and local-vs-API break-even need user-supplied inputs, next |
| 6 | Serverless AWS deployment, documented — "operating this costs $0.40/month" | ⏳ |
| 7 | PR to `focus_converters` + bilingual (EN/ES) case study | ⏳ |

## Limitations (honest)

- Local-log sources only so far (Claude Code, Codex CLI, Kimi Code); billed-cost
  admin-API ingesters are pending.
- Codex records are taken from `token_count` events as reported, with only a
  consecutive-duplicate guard — no cross-file dedup yet.
- Costs are list-price showback, not invoices. Subscription plans bill differently.
- Older log formats don't break down cache writes by TTL; totals are attributed to the
  5-minute tier (slight underestimate), documented in the parser.
- Daily buckets use the UTC dates recorded in the logs; a late-night local session can
  land on the next UTC day.
- The export passes the Foundation's `focus-validator` (spec 1.2) in CI and is
  cross-checked against the Foundation's official sample data (which targets FOCUS 1.0;
  the tests assert convention compatibility, not column equality).

## Transparency

Built with AI assistance (Claude). Policy: nothing is merged that the maintainer does
not fully understand and stand behind.

## License

[MIT](LICENSE)
