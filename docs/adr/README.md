# Architecture decision records

Each record states one decision: the context that forced it, what was
decided, and what it costs. They are dated by when the decision was made
and kept when superseded, so the reasoning behind every number tokencur
prints can be traced. Format: [Michael Nygard's ADRs](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions).

| # | Decision | Status |
|---|---|---|
| [0001](0001-showback-and-three-money-concepts.md) | Value usage as list-price showback; keep outlay, value and counterfactuals apart | Accepted |
| [0002](0002-two-pricing-layers-never-zero.md) | Two pricing layers, curated card first; unpriced is never $0 | Accepted |
| [0003](0003-focus-1-2-gated-by-the-foundation-validator.md) | Target FOCUS 1.2, gated in CI by the Foundation's validator | Accepted |
| [0004](0004-local-ledger-with-audited-corrections.md) | A local SQLite ledger keyed on stable ids, with audited corrections | Accepted |
| [0005](0005-reconcile-with-the-sources-own-counters.md) | Reconcile with a source's own counters wherever it keeps them | Accepted |
| [0006](0006-metadata-only-and-no-runtime-dependencies.md) | Read metadata only, depend on nothing at runtime | Accepted |
| [0007](0007-current-list-rates-not-point-in-time.md) | Value history at current list rates, not point-in-time rates | Superseded by 0008 |
| [0008](0008-point-in-time-list-rates.md) | Value each call at the list rate in force on its day | Accepted |
| [0009](0009-billed-charges-next-to-showback.md) | Billed charges sit next to showback, never mixed into it | Accepted |
| [0010](0010-usage-value-per-commit.md) | Usage value per commit: attribute each call by where it ran | Accepted |
| [0011](0011-curated-rates-change-only-on-the-record.md) | Curated rates change only through dated history or a declared correction | Accepted |
