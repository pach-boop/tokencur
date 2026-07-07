# tokencur

**The CUR for your tokens** — an open-source pipeline that converts multi-provider AI/LLM
usage data (Anthropic, OpenAI, Gemini, coding agents, local models) into
[FOCUS](https://focus.finops.org)-conformant cost datasets, with unit economics and a
savings-recommendation engine.

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
   computed as *API-equivalent list cost* from a versioned rate card
   ([`pricing.py`](src/tokencur/pricing.py), dated and sourced). Unknown models surface as
   *unpriced usage* rather than silently costing $0.
4. **Explainable over clever** — no line ships that the maintainer can't explain in an
   interview.

## Quickstart

Requires Python 3.11+. No dependencies for the v0 report.

```bash
python -m tokencur.report ~/.claude/projects
```

Prints per-model and per-day API-equivalent cost of your local Claude Code usage,
including cache-tier economics (reads at 0.1x, writes at 1.25x/2x input rate).

## Roadmap

| Phase | Deliverable | Status |
|---|---|---|
| 1 | Repo, thesis, related work | ✅ |
| 2 | Ingest real usage: Claude Code logs; Anthropic/OpenAI/Gemini exports | 🔨 Claude Code done |
| 3 | FOCUS normalizer (Python) with tests against official sample data | ⏳ |
| 4 | DuckDB + Streamlit dashboard: trends, top spend, unit economics | ⏳ |
| 5 | Recommendation engine: model right-sizing, caching ROI, batch vs realtime, local-vs-API break-even | ⏳ |
| 6 | Serverless AWS deployment, documented — "operating this costs $0.40/month" | ⏳ |
| 7 | PR to `focus_converters` + bilingual (EN/ES) case study | ⏳ |

## Limitations (honest)

- Only one data source so far (Claude Code local logs).
- Costs are list-price showback, not invoices. Subscription plans bill differently.
- Older log formats don't break down cache writes by TTL; totals are attributed to the
  5-minute tier (slight underestimate), documented in the parser.
- Not yet validated against FOCUS sample datasets — that is phase 3.

## Transparency

Built with AI assistance (Claude). Policy: no line is merged that the maintainer cannot
explain in an interview.

## License

[MIT](LICENSE)
