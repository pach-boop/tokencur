# Landscape research — what tokencur borrows, integrates, or must study

Curated 2026-07-06. Each entry says exactly what this project takes from it and in
which roadmap phase it lands. Keep this honest: tokencur stands on these shoulders.

## 1. Pricing — stop hand-maintaining the rate card (phase 2b)

**[LiteLLM `model_prices_and_context_window.json`](https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json)**
([repo](https://github.com/BerriAI/litellm)) — community-maintained pricing for every
major provider (Anthropic, OpenAI, Gemini, Bedrock…), including cache-creation and
cache-read rates per model. This is how [ccusage](https://github.com/ryoppippi/ccusage)
prices usage.

**Take:** add a pricing loader that reads a *pinned, vendored snapshot* of this JSON
(reproducible builds, no network dependency), refreshed deliberately via a small update
script. Keep `pricing.py`'s hand-written card as the override layer and the documented
source of truth for showback methodology.

**Copy the pattern, not the code:** ccusage's offline-first pricing with explicit
online refresh, and user-defined per-model price overrides via config file.

> **Status: integrated 2026-07-06** — vendored snapshot (212 models) +
> `scripts/update_pricing_snapshot.py`; curated card takes precedence. User-defined
> overrides remain future work.

## 2. Validation — prove FOCUS conformance, don't claim it (phase 3)

**[focus_validator](https://github.com/finopsfoundation/focus_validator)** — the FinOps
Foundation's own validator for checking datasets against the FOCUS spec.

**Take:** run the validator against tokencur's output in CI. "Validated by the
Foundation's own tooling on every commit" is the strongest conformance claim available
to an independent project.

**[FOCUS_Spec](https://github.com/FinOps-Open-Cost-and-Usage-Spec/FOCUS_Spec)** — the
spec source, including the token/credit billing columns and the OpenAI usage example.
Normalizer column mapping must cite spec sections in docstrings.

## 3. Upstream target — mirror their architecture before the PR (phase 7)

**[focus_converters](https://github.com/finopsfoundation/focus_converters)** — converts
AWS/Azure/GCP/OCI billing exports to FOCUS. Conversion rules live as **YAML files in
`conversion_configs/`, applied in order** — not as imperative code.

**Take:** study `CONTRIBUTING.md` and one existing provider's config tree before
designing the phase-3 normalizer, so tokencur's internal mapping can be exported as a
`conversion_configs/anthropic/` contribution with minimal rework. Designing against the
upstream shape from day one is the difference between "PR merged" and "PR rewritten".

## 4. Data sources beyond local logs (phase 2)

**[Anthropic Usage & Cost Admin API](https://docs.anthropic.com/en/api/usage-cost-api)** —
`/v1/organizations/usage_report/messages` (tokens by model/workspace/service tier,
1m/1h/1d buckets) and a [cost report endpoint](https://docs.anthropic.com/en/api/admin-api/usage-cost/get-cost-report)
(daily buckets). Requires an Admin API key. There is an
[official cookbook](https://platform.claude.com/cookbook/observability-usage-cost-api).
This is the *billed* (not showback) source for API organizations — the ingester that
makes tokencur useful to organizations, not just to individuals.

**OpenAI organization usage/costs endpoints** — same role for OpenAI orgs; pairs with
FOCUS's official OpenAI token example.

**Other coding agents' local logs** — [ccusage](https://github.com/ryoppippi/ccusage)
(and forks like [ccost](https://github.com/carlosarraes/ccost)) already parse
Claude Code, Codex CLI and Gemini CLI local histories. **Take:** their documented log
locations and formats as the spec for tokencur's next local ingesters; credit them in
Related Work.

> **Probed 2026-07-06/07 on the maintainer's machine:** `~/.codex` holds ~280MB of real
> local history (`history.jsonl` + session data) — a ready-made second dataset for the
> Codex ingester. `~/.kimi-code` holds real Kimi CLI sessions (`sessions/` +
> `session_index.jsonl` + `user-history/*.jsonl`) — dataset #3. `~/.gemini` held
> configuration only, no usage logs.

## 5. Reference implementations to study, not copy

- **[OpenCost's OpenAI plugin](https://opencost.io/docs/integrations/plugins/openai)** —
  the closest FOCUS-conformant open implementation. Study its token→FOCUS column
  mapping decisions before writing ours; diverge knowingly, and document divergences.
- **Finout / Vantage / CloudZero** — closed-source proof that enterprises pay for
  exactly this pipeline. Market signal for the case study, nothing to import.
- **Observability integrations (Datadog, Elastic, Honeycomb)** all ship Anthropic
  usage/cost pollers — evidence that the Admin API ingester (item 4) is the
  highest-demand connector to build first.

## Priority order (judgment)

1. **focus_validator in CI** — cheapest, highest-credibility win; unblocks honest
   "FOCUS-conformant" claims (phase 3 gate).
2. **LiteLLM-backed pricing loader** — removes the biggest maintenance liability
   before multi-provider ingestion lands.
3. **Anthropic Admin API ingester** — first *billed-cost* source, the connector
   organizations actually need.
4. **focus_converters architecture study** — one session, before the normalizer design.
5. Codex/Gemini local-log ingesters — after the FOCUS core exists.
