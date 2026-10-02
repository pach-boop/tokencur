# 0009. Billed charges sit next to showback, never mixed into it

- Status: Accepted
- Date: 2026-10-02

## Context

Everything tokencur produced so far was showback: usage valued at list
price, because the coding agents run on flat subscriptions (ADR 0001).
The review listed "more providers" and "API billing exports" as the gap
between a prototype and a financial system of record. The first billed
source at hand was RunPod: GPU pods billed per hour from prepaid
credits, $2.69 on the maintainer's account between April and October
2026, with a REST billing API that returns one row per pod and day.

A charge is a different kind of fact from a usage record. The provider
states the amount; tokencur does not compute it. Summing the two would
add money that left the account to a valuation of work that did not
cost that, which is the conflation ADR 0001 exists to prevent.

## Decision

- A separate record type, `BilledCharge`: period, provider, service,
  resource, quantity and unit, and the amount billed.
- A separate ledger table, `charges` (schema 4), deduplicated on
  `(source, record_id)` like usage. Imports are explicit and offline:
  `tokencur import runpod FILE` reads an export that
  `scripts/fetch_runpod_billing.py` saves, so tokencur itself still
  makes no network calls.
- In FOCUS, charges become Compute rows whose BilledCost is the billed
  amount. With no list price published per charge, ListCost equals it;
  a storage-only charge has no unit price (null).
- Reports keep two totals: "API-EQUIVALENT TOTAL (showback)" and
  "BILLED (real money, from provider bills)". The observatory shows
  provider bills as actual money but leaves them out of subscription
  leverage, since they buy compute, not coding-agent usage.

## Consequences

- tokencur's export can now hold real billed cost and showback side by
  side, each labeled, the way a FinOps dataset mixes invoiced and
  allocated cost.
- Each billed source needs its own importer; Anthropic's and OpenAI's
  admin cost APIs are next (#7), waiting on real response samples.
- RunPod's rows mix GPU time and disk in one amount; the split is not in
  the export, so the description names the disk size and the quantity
  is GPU hours.
