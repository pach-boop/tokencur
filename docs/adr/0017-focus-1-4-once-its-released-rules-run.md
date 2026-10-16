# 0017. Target FOCUS 1.4 once the validator runs its released rules

- Status: Proposed (supersedes ADR 0016 when accepted)
- Date: 2026-10-15

## Context

FOCUS 1.4 was published on 2026-06-04. Its rule file, `model-1.4.json`
in the v1.4 release, is the one `focus-validator` downloads for 1.4 by
default. Version 2.2.1, the newest, crashes on it before checking any
data ("Active-edge cycle detected"). The Foundation's 1.4.0.1 rules, a
prerelease of 2026-10-09, run.

What 1.4 changes for tokencur's Cost and Usage export:

- Two new columns: `CommitmentProgramEligibilityDetails`, for commitment
  programs, and `InvoiceDetailId`, for payable invoices.
- Two new datasets for payable invoices, BillingPeriod and InvoiceDetail.
- None of the seven new applicability criteria applies to tokencur's
  data: commitment programs, payable invoices, purchase orders, partial
  upfront payment, and differences of currency or aggregation between
  pricing, billing and payment.
- The text of 1.4 removes `ProviderName` and `PublisherName`
  (`ServiceProviderName` is "a replacement for the removed ProviderName
  column"), and a column FOCUS does not define must carry the `x_`
  prefix. Yet the rule files, released and prerelease, still require
  both columns, with two presence rules in status "Deprecated", the only
  rules in that status. The validator runs them, so an export without
  those columns fails both.

## Decision

On the `focus-1.4` branch, tokencur exports the FOCUS 1.4 Cost and Usage
dataset:

- The two new columns are explicit nulls: tokencur's sources have no
  commitment programs and no payable invoices.
- `ProviderName` and `PublisherName` stay, as in 1.3 (the maintainer's
  choice, 2026-10-15). The validator then passes with nothing tolerated,
  and no reader of the export loses a column. ADR 0016's rule holds:
  tolerate a rule by name only when no dataset can pass it.
- BillingPeriod and InvoiceDetail are not exported. tokencur is
  showback, and its one billed source, RunPod's prepaid credits, issues
  no payable invoice.
- The branch's gate runs the validator on the 1.4.0.1 rules, pinned by
  SHA-256, with the nine applicability criteria of ADR 0016: 351 rules
  pass and none fails.

The branch is merged when the validator runs released 1.4 rules and the
export passes them. The weekly `focus-watch` action reports that day. On
merging, the gate pins the released rule file, this ADR is accepted and
supersedes ADR 0016, and the watch's 1.3 check retires.

## Consequences

- Being ready for 1.4 costs a branch kept open and rebased on main when
  the export changes.
- The export grows from 65 columns to 67, two more than the text of 1.4
  lists. Those two can go once the rule files drop their presence rules.
- Until the merge, the branch is checked against prerelease rules, which
  the Foundation can replace. The hash pin makes a replacement visible,
  and the watch reports a newer prerelease file.
