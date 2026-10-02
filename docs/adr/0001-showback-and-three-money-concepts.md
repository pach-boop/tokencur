# 0001. Value usage as list-price showback; keep three money concepts apart

- Status: Accepted
- Date: 2026-07-06, extended 2026-07-17

## Context

The first data source, the maintainer's coding agents, runs on flat
subscriptions (Claude, ChatGPT, Kimi). Nothing is billed per token, so
there is no invoice to normalize. Yet "what did this work consume" is the
question FinOps exists to answer, and a token count alone answers nothing.

From the first version tokencur valued usage at API list prices, but its
outputs still spoke of savings ("caching saved $3,574"). In July the
maintainer objected: no money had been spent or saved; a subscription
had been paid. A tool that says "you spent $1,200" when $50 left the
bank account is wrong in the way that matters most in finance, and the
same conflation runs through most AI cost dashboards.

## Decision

tokencur computes **API-equivalent list cost**, the standard showback
method: what the same usage would cost at published per-token rates. It
keeps three money concepts apart, in code, output and documentation:

1. **Actual outlay**: subscription fees really paid, declared in
   `subscriptions.json`. The only real money.
2. **Usage value (showback)**: list-price valuation of the usage.
3. **Counterfactuals**: cost avoided by provider caching, and what-if
   right-sizing. Properties of the workload, not actions taken.

Value divided by outlay gives *subscription leverage*. In the FOCUS export,
all four cost columns carry the list cost and `InvoiceId` is an explicit
null, because no invoice exists.

## Consequences

- Every label says which concept a number is: "API-EQUIVALENT TOTAL
  (showback)", "avoided (counterfactual)", "headroom (what-if)".
- The headline figure is not a bill and cannot be reconciled against one.
  Billed-cost sources (API invoices, GPU rentals) will need real
  `BilledCost` and an `InvoiceId`, side by side with showback rows.
- Under a flat fee, right-sizing buys rate-limit headroom, not dollars;
  the recommend output says so.
