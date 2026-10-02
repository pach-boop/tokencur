# 0002. Two pricing layers, curated card first; unpriced is never $0

- Status: Accepted
- Date: 2026-07-06; retired-model retention 2026-10-01; cross-check 2026-10-02

## Context

Valuing usage needs a rate for every model the agents call, across
providers whose cache economics differ (Anthropic reads cache at 0.1x
input, or 0.05x on Opus 5.5 and 0.025x on Fable 5.1; OpenAI at 0.5x).
Hand-maintaining every rate does not scale; trusting one community
database alone makes every number hostage to its errors. And the most
dangerous failure in a cost tool is silent: an unknown model valued at $0
makes the total look right while it is wrong.

## Decision

- **Layer 1, the curated card** (`pricing.RATE_CARD`): Anthropic's
  pricing page, transcribed with a date and a source, plus documented
  proxy rates where no price is published. It always wins.
- **Layer 2, a vendored snapshot** of LiteLLM's community price
  database, refreshed daily by the price-watch action and committed, so
  pricing is reviewable, reproducible and offline. Cache rates come from
  explicit per-model fields, never from a universal multiplier.
- **Unpriced is never $0.** A model in neither layer surfaces as unpriced
  usage in every output and is excluded from the FOCUS export.
- **Retired models keep their last rate.** When LiteLLM drops a model,
  the snapshot keeps it, flagged `retired_upstream`, so old usage stays
  priced. Learned the hard way: 56 models vanished from the snapshot
  before this rule existed.
- **The layers are cross-checked.** A test fails whenever the curated
  card and the snapshot disagree on a model both price. The curated card
  had valued Sonnet 5 at $3/$15 after its $2/$10 launch price became
  standard; the snapshot was right.

## Consequences

- A provider moving a curated price stops the daily refresh until the
  card follows: deliberate friction, since the card is the layer that
  wins.
- The snapshot is third-party data inside the package. Its git history
  is the audit trail, published as the price card's change log.
- Rates are current, not point-in-time: see ADR 0007.
