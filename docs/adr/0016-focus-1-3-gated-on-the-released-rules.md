# 0016. Target FOCUS 1.3, gated by the Foundation's validator on its released rules

- Status: Accepted (supersedes ADR 0003)
- Date: 2026-10-15

## Context

ADR 0003 targeted FOCUS 1.2 because the Foundation's `focus-validator`
shipped rules for 1.2 only. Its version 2.2.1 now downloads newer rule
files from the FOCUS_Spec releases. With 1.3 (spec of 2025-12-08) it
runs. With 1.4 (2026-06-04, the newest) it crashes before checking any
data. On 2026-10-14 the maintainer chose to move to 1.3 now and be
ready for 1.4.

The 1.3 rules failed 166 rules on the 1.2 export:

- Two columns were new and mandatory: `ServiceProviderName` and
  `HostProviderName`. They split `ProviderName`, which 1.3 deprecates
  but still requires.
- Nearly all the rest were type checks on columns for features
  tokencur's sources lack. The validator checks a column's type even
  where its feature does not apply.
- One failure is not ours. In the released rule file
  (`model-1.3.0.1.json`), the condition of
  `CapacityReservationStatus-C-003-C` and `C-004-C` is malformed
  (`{"AND": ...}` instead of a check function). The validator then
  applies both rules to every row: one requires a status, the other
  requires none, so no dataset passes both. The Foundation's newer
  1.3.0.4 rules, published as a prerelease on 2026-10-09, fix it.

## Decision

tokencur exports every column of the FOCUS 1.3 Cost and Usage dataset.
CI fails unless the export passes the validator on the Foundation's
released 1.3 rules, the file the validator downloads for 1.3 by default.

- Columns for features no source has are explicit nulls: cost
  allocation, zones and regions, capacity reservations, commitment
  discounts, contracts and tags.
- Each cost is its unit price times its quantity, because the validator
  checks that product with no tolerance. A billed charge keeps what the
  provider took in `BilledCost` and `EffectiveCost`.
- The gate pins the rule file by SHA-256 and runs the validator on it
  offline, so a re-published file cannot change the verdict unseen.
- The gate declares the nine applicability criteria tokencur's data
  meets; the script names each one with its reason. Rules for those
  features run instead of being skipped: 395 rules pass, against 136 at
  the end of ADR 0003.
- One rule is tolerated by name, with the composite rule that fails
  only through it: `CapacityReservationStatus-C-004-C`. The 1.2
  tolerance (`InvoiceId-C-005-C`) is gone, because 1.3 skips those
  rules.

The export passes the 1.3.0.4 prerelease with no failure, but the gate
uses the released rules (the maintainer's choice, 2026-10-15). Anyone who
runs the validator on 1.3 gets the released file, so the gate shows what
they would see. A prerelease asset can also be replaced at any time.

## Consequences

- "FOCUS 1.3 conformant" is a tested claim, with one named exception
  that a defect in the rule file explains.
- The gate needs the network to fetch the rule file, as pip does to
  install the validator.
- The export grows from 35 columns to 65, most of the new ones empty.
- Moving to 1.4 waits for released rules the validator can run. The
  1.4.0.1 prerelease already runs: the export fails it only for two
  columns new in 1.4.
