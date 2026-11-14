# 0018. Billed AI cost from saved admin cost reports, one grouping per day

- Status: Accepted
- Date: 2026-11-14

## Context

ADR 0009 brought billed cost into tokencur with RunPod and left
Anthropic's and OpenAI's admin cost APIs next (#7). Their documentation,
read on 2026-11-13, describes three response shapes:

- Anthropic `cost_report`, marked Legacy but still supported: daily
  buckets of results, each an amount in cents written as text, split by
  the fields it was grouped by (description, model, token type, context
  window, service tier, speed, inference region, workspace). No token
  counts and no request counts.
- Anthropic `analytics/cost`, a beta: flat rows with a `group` object and
  a `cost` object holding the amount after discounts and before credits,
  the amount at list price, and a request count (null when grouped by
  cost or token type).
- OpenAI `organization/costs`: daily buckets of results, an amount with a
  currency, split by line item or project. The spec does not state the
  unit of the amount; its example reads as dollars.

No real export was at hand, and the maintainer's API key must not be used
to fetch one. Two problems needed a decision.

First, FOCUS wants a quantity on every usage row. `ConsumedQuantity` and
`PricingQuantity` may be null only on corrections, and `SkuPriceId`, which
needs them, is required on usage. Most of these lines come with money and
nothing to count.

Second, the same money can be reported several ways: by model or by
workspace, per hour or per day, by the legacy or the beta endpoint. Each
way splits the same total differently, so lines from two of them have
different identities and both would be kept. Summing them counts that
money twice.

## Decision

- `tokencur import anthropic-cost FILE` reads both Anthropic shapes and
  tells them apart by their rows. `tokencur import openai-costs FILE`
  reads OpenAI's, with both names its spec gives the results array, and
  takes the amount as dollars. They read saved files only: a single
  page, a list of pages, or either wrapped the way
  `scripts/fetch_runpod_billing.py` wraps its responses. tokencur still
  makes no network calls, and there is no fetch script, because fetching
  needs an admin key the project does not handle.
- Each line becomes a `BilledCharge`, with the same rules as RunPod's: a
  line whose amount is not money in USD is skipped, never guessed at.
  Its record id is its grouping, its period and every field it was split
  by, so a reimport updates amounts (the beta's values can change for a
  week after month end) and never adds a copy.
- The quantity is the request count when the report has one. Otherwise
  it is the line's period in hours, and the description says the report
  had no quantity. Every number in the row comes from the report, and
  `cost = unit price × quantity` holds as FOCUS checks it.
- The beta's list amount is kept in a new column, `list_usd` (ledger
  schema 9), and becomes FOCUS `ListCost`. Charges without one keep
  ListCost equal to the billed amount, as before.
- In FOCUS these rows are "AI and Machine Learning" / "Generative AI", one
  SKU per model or line item, under an "API organization" account.
- An import that would put a second grouping on days the ledger already
  holds for that provider, or that holds two groupings for the same days
  itself, is refused whole, and the error names the day and both
  groupings. The grouping comes from the saved request URL, or from the
  fields the lines are split by when no URL was saved, so the same report
  reads the same way either way.

## Consequences

- The FOCUS gate now validates Anthropic and OpenAI billed rows too,
  including rows whose list price sits above what was billed.
- The fixtures follow the documented shapes, unverified against a real
  export, and the README says so. The first real export may need a fix.
- A bill's own quantity is lost when the report has none: tokens and
  requests stay in the showback rows from the agents' logs, not here.
- Workspaces and projects are named in each charge's description, not in
  FOCUS `SubAccountId`, since a charge has no field for them yet.
- There is no command to remove imported charges. A user who imported the
  wrong grouping keeps it, and imports the other grouping only for days
  it does not cover.
- Anthropic's credits and Priority Tier commitment fees are not in these
  reports, so they are not in tokencur either.
