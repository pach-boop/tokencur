# Test fixtures

## focus_sample_official_slice.csv

First 30 rows of `FOCUS-1.0/focus_sample.csv` from the FinOps Foundation's
[FOCUS-Sample-Data](https://github.com/FinOps-Open-Cost-and-Usage-Spec/FOCUS-Sample-Data)
repository, © FinOps Foundation, licensed under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Unmodified except for
truncation.

Used by `tests/test_focus_cross.py` to cross-check tokencur's export conventions
against an official FOCUS dataset (note: the sample targets FOCUS 1.0; tokencur
exports 1.2, which adds columns such as `InvoiceId` and `ServiceSubcategory`).
