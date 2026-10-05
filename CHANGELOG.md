# Changelog

All notable changes to tokencur are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) · Versioning: [SemVer](https://semver.org).

## [Unreleased]

### Fixed

- `tokencur outcomes` and `outcomes --sessions` now name what reproduces
  their figures, as the report, the export and the observatory already
  did: the tokencur version, the curated card's date and the pricing
  snapshot's SHA-256.
- In a partial clone, `tokencur outcomes` could make git download the
  objects the clone left out: a network call SECURITY.md says never
  happens. Its git commands now run with `GIT_NO_LAZY_FETCH`, so with
  git 2.45 or newer such a repository is reported as unreadable instead.
  `tokencur prices` still lets git download the snapshot's past versions,
  which its page needs, and SECURITY.md says so.
- SECURITY.md describes the log reads, git commands and writes as the
  code makes them: request and message ids, Kimi Code's `state.json`,
  author names, `Co-authored-by` trailers and lines changed, diffs read
  only as `git patch-id` input, `git rev-parse` and `git symbolic-ref`,
  the git history `tokencur prices` reads, what a RunPod import keeps,
  and the ledger's backup before a schema upgrade. 0.5.0 said prompts
  are told apart by each message's origin; tokencur also reads each
  line's flags and, on lines from before Claude Code recorded an origin,
  whether the content is plain text and the types of its blocks, never
  the text. Lines are decoded as JSON; message content never reaches a
  record. CONTRIBUTING says the same.
- ADR 0012 said Codex and Kimi Code record no timings. Kimi Code times
  each model step and Codex 0.125 each turn, though neither keeps a
  session total with and without retries. A dated update says so, and
  names the two reads the decision left out.

## [0.5.0] - 2026-10-02

### Added

- **Usage value per successful change, session by session** (ADR 0012,
  closes #15). `tokencur outcomes --sessions` shows each agent session's
  usage value, the prompts a person typed, its API time and the share
  lost to retries (Claude Code), and its changes: landed on the default
  branch, reverted, or pending. A change goes to the last session that
  worked in its repository in the day before; a rebased copy is the same
  change, by patch id; a revert commit is a correction, not a change.
  The headline is usage value per change that landed and stayed: $1.89
  on tokencur's own repository.
- Claude Code's counters now include each session's API time, with and
  without retries, and the prompts a person typed, read from each
  message's origin, never its text.

### Changed

- The git plumbing behind `outcomes` moved to `tokencur.gitlog`, shared
  by the repository and session views. `outcomes.OutcomesError` is the
  same class as `gitlog.GitError`.
- Terminal tables can align several label columns to the left.

## [0.4.0] - 2026-10-02

### Added

- **Curated rates can only change on the record** (ADR 0011). Every rate
  the hand-maintained Anthropic card holds is recorded, and a test fails
  when one leaves the card any way but into its dated history, a price
  move, or through a declared correction of a rate that never applied.
  Tests also show that a September figure never moves when an October
  price does, for curated and snapshot rates alike. Replaying every
  version of the card in git found no curated price move so far; the one
  change, Sonnet 5 in 0.3.0, is now declared as a correction.
- **Reconciliation with Claude Code's own counters** (ADR 0005, updated).
  When a session ends, Claude Code writes the cost it counted for each
  model, including calls it never writes to the transcript. `tokencur
  doctor` compares those counters with tokencur's value of the same
  sessions, treating a chain of continued sessions as one, and flags
  coverage below 85% (calls going missing) or above 105% (calls counted
  twice). On the maintainer's sessions tokencur sees 93.6%: the rest is
  internal calls for web search, web fetch and session titles, plus
  main-model calls such as compaction. The README states the gap.
- **Reproducible figures.** The report's second line, the export's
  closing message and the observatory's footer and `data.json` name what
  produced the numbers: the tokencur version, the curated card's date
  and the pricing snapshot's SHA-256, the same file each release
  attests. `pricing.provenance()` is the one source; `doctor` uses it
  too.

### Fixed

- The README's two links to the price-watch workflow were relative, so
  they broke on PyPI, which shows the README as the project page. They
  are absolute now, like the rest.

## [0.3.1] - 2026-10-02

### Added

- **Subscription plans with dates.** `subscriptions.json` can list each
  service's plans with the days they were active (`from` included,
  `until` excluded): an upgrade, a cancellation, a late start. The
  observatory counts each fee only while its plan was active, shows the
  plans in words with any assumption behind their dates, and reports the
  fees paid on the window's last day as "subscriptions / month now". A
  flat `monthly_usd` fee still reads as one plan across the whole window.

### Changed

- **On PyPI:** `pip install tokencur`. Each version tag now publishes to
  PyPI through trusted publishing, with no stored token; 0.3.0 was the
  first. The package declares its license as an SPDX expression, with
  project links and classifiers, and the README's links are absolute so
  they also work on PyPI.

### Fixed

- The published observatory overstated what the maintainer pays. It
  counted all three subscription fees across the whole window, while
  Codex CLI's plan ended in April, Kimi Code's ran from June to
  September, and Claude Code's started on 2026-09-24 and moved from $20
  to $100 on 2026-10-02. For 2026-02-07 to 2026-10-02, estimated outlay
  goes from $240.49 to $96.59 and subscription leverage from 3.5x to
  8.7x. The page discloses the correction.

## [0.3.0] - 2026-10-02

### Added

- **Usage value per commit** (ADR 0010), the first unit-economics layer:
  `tokencur outcomes [REPO...]` sets each git repository's usage value
  against its commits, with agent-signed commits and lines changed beside
  them, and reports how much usage it could not attribute and why. Each
  call is attributed by the directory it ran in, which every ingester now
  reads: Claude Code per line, Codex per turn, Kimi Code per session
  (ledger schema 5, local only). On the maintainer's logs, 71% of Claude
  Code calls ran outside the directory their session started in. `doctor`
  shows how many records name their directory.
- **Billed cost, starting with RunPod** (ADR 0009, closes the RunPod
  half of the "API billing exports" gap). `scripts/fetch_runpod_billing.py`
  saves the billing history from RunPod's REST API (it replaces the
  GraphQL probe, whose `dailyCharges` query came back empty);
  `tokencur import runpod FILE` keeps it in a new ledger table
  (schema 4) as `BilledCharge` records. Reports show a separate
  "BILLED (real money, from provider bills)" total; the FOCUS export
  adds Compute rows whose `BilledCost` is the billed amount (validated
  in CI); the observatory shows provider bills as actual money, outside
  subscription leverage; `doctor` counts them. On the maintainer's
  account: 20 charges, $2.69.
- Negotiated discounts: `--discounts FILE` on `report` and `export`
  (`{"discounts": {"Anthropic": 0.15}}`, a fraction off list per FOCUS
  provider). The FOCUS export keeps the public price in `ListCost` and
  puts the contracted one in `ContractedCost`, `EffectiveCost` and
  `BilledCost`; the report adds a contracted total. A malformed file is
  a one-line error. The CI conformance gate validates a discounted
  export too.
- `tokencur report --currency CODE --fx-rate RATE` shows the totals in
  another currency at a rate you give; nothing is fetched, and the
  FOCUS export stays in USD, the currency providers bill in.
- Each release publishes the pricing snapshot as its own asset,
  `tokencur-pricing-snapshot-X.Y.Z.json`, with a build provenance
  attestation, so the prices behind every figure are versioned and
  verifiable; `tokencur doctor` prints the installed snapshot's
  SHA-256 to compare. SECURITY.md shows how to verify both.
- Fixtures from real agent logs (`tests/fixtures/logs-real`): a Claude
  Code 2.1.282 session, Codex 0.98.0 and 0.104.0 rollouts and a Kimi
  Code wire log, redacted by `scripts/redact_log.py` to an allowlist
  of usage fields with pseudonymous ids, shifted times and no content.
  The script refuses to write unless the ingester reads the same usage
  from the redacted file as from the original; a privacy test fails on
  any string that is not an allowed shape (checked against an injected
  leak). Their golden records and FOCUS CSV join the golden tests, and
  the CI conformance gate now validates them too.
- Request options that change a call's price, as Claude Code logs them:
  fast mode (2x: Opus 5.5 $8/$40), US-only inference (1.1x on every
  category) and the Batch API (0.5x), cache multipliers stacked on top.
  Records carry `price_modifiers`; the FOCUS export gives each option
  its own `SkuPriceId` and `ListUnitPrice`; right-sizing prices the
  cheaper model with the same region and batch options at standard
  speed. Ledger schema 3 adds the column; stored rows read as
  standard. None of the maintainer's calls used an option.
- `tokencur doctor`: a read-only health check. Per log source it
  reports files, usage lines, records, unreadable lines and the agent
  versions the logs name, and flags a likely format change; it checks
  the ledger's schema and SQLite integrity and prints the pricing
  snapshot's SHA-256. Exits 1 on any problem. A damaged or
  newer-schema ledger now gives every command a clear error and a
  recovery hint instead of a traceback.
- The observatory discloses corrections to figures it published
  before: a dated note at the top (and a `corrections` list in
  `data.json`) saying what changed and by how much, starting with the
  Codex double count ($685.66 → $332.95).
- Release workflow: a `vX.Y.Z` tag that matches `__version__` builds
  the sdist and wheel, attests their build provenance (verifiable with
  `gh attestation verify`) and drafts a GitHub release with them.
  PyPI trusted publishing is wired but off until the maintainer
  registers the publisher and sets `PUBLISH_TO_PYPI`. The CI lint job
  now also runs actionlint, with shellcheck, over every workflow.
- `scripts/benchmark.py`: a reproducible benchmark that times scan,
  ledger write, rescan, ledger read, report and FOCUS export on N
  synthetic messages. One million messages: linear time, 6.3 s to
  scan, 56 s to export 4M FOCUS rows; numbers in the README.
- Architecture decision records in `docs/adr`: showback and the three
  money concepts, the two pricing layers, FOCUS 1.2 behind the
  Foundation's validator, the ledger and its audited corrections,
  reconciling with a source's own counters, metadata only with no
  runtime dependencies, and current rather than point-in-time rates.
- Property-based tests (Hypothesis, dev extra): generated records,
  histories and log files check the promises the numbers rest on.
  FOCUS rows add up to the record's cost; every row is internally
  consistent (periods, unit price × quantity, the four cost columns
  equal under showback). Cost is linear in tokens. A split day
  divides a history without loss or overlap. The ledger keeps every
  record exactly once. Fingerprints ignore key order. Codex counts
  reconcile with the running total under random re-sends. A streamed
  Claude message keeps each field's largest count.
- Golden tests: synthetic logs shaped like each agent's real logs
  (`tests/fixtures/logs`), covering every case that broke or nearly
  broke an ingester, with the exact records and FOCUS CSV they must
  produce (`tests/fixtures/golden`). The CI conformance gate now runs
  the FinOps Foundation validator on that export too, so it checks
  what the real ingesters produce from real-shaped logs, not only
  hand-built records.
- Quality gates in CI: a ruff lint and format job (ruff pinned in the
  dev extra, with a matching pre-commit hook), the suite on macOS and
  Windows besides Linux 3.11 to 3.13, branch coverage measured with
  every extra installed and a 90% floor (94% today), and a package
  job that builds the sdist and wheel, runs `twine check`, and proves
  the installed wheel carries the console script and the pricing
  snapshot. Test connections to SQLite are now closed, and pytest
  turns any leaked handle into a failure.
- A real command line: `tokencur` is installed as a console script
  (`python -m tokencur` still works), with `--help` and `--version`
  on one argparse parser for `report`, `export`, `recommend`,
  `observatory` and `prices`. `report`, `export` and `recommend` take
  `--since` / `--until` (UTC days, end excluded, as billing periods
  are cut), so one month exports as its own FOCUS file. The old
  module entry points (`python -m tokencur.report`...) delegate to it.
- Project governance: `CONTRIBUTING.md` (ground rules, how to add a
  source), `SECURITY.md` (what tokencur reads and writes, private
  vulnerability reporting), a Contributor Covenant code of conduct,
  issue and pull request templates and `CODEOWNERS`. Dependabot
  (Actions and Python extras), Dependabot security updates, private
  vulnerability reporting, CodeQL code scanning and an OpenSSF
  Scorecard workflow are enabled. Workflows are pinned to commit SHAs
  and run with least-privilege permissions, timeouts and
  cancel-in-progress concurrency; `ci` runs on pull requests and on
  pushes to `main`.
- Ledger: every command — `report`, `export`, `recommend`, the
  observatory and the dashboard — now keeps what it scans in a local
  SQLite ledger and reports the ledger's full history, so totals no
  longer shrink when coding agents delete old logs (Claude Code removes
  transcripts after `cleanupPeriodDays`, 30 by default). Usage events
  are deduplicated on a stable `record_id` built from raw source
  fields; events whose logs are gone keep their last known values.
  Metadata only, owner-readable file, schema-versioned, stdlib
  `sqlite3` (still no runtime dependencies). Location:
  `$TOKENCUR_LEDGER`, else `$XDG_DATA_HOME/tokencur/ledger.sqlite3`.
- Observatory history gaps: `subscriptions.json` can declare
  `history_gaps` (usage that happened but whose logs were lost before
  the ledger existed). The page discloses each gap in a note at the
  top, and the affected subscription fee is not counted across it, so
  lost records don't depress subscription leverage. Fees still count
  over the whole window everywhere else — a paid month with no usage is
  real money.

- Price card page: `python -m tokencur.prices` renders `docs/prices/`
  (published via GitHub Pages) — the curated Anthropic rates, the
  community-snapshot coverage count, and a dated change log built from
  the git history of the snapshot file. The daily price-watch action
  commits only on a real rate move, so the timeline is that action's
  public face. It distinguishes introduction / expansion (models added)
  / rate move; the pure `diff_models` carries the test weight.

- Observatory: `python -m tokencur.observatory` renders the FOCUS
  dataset as a self-contained static dashboard (`docs/observatory/`,
  published via GitHub Pages) — KPIs, daily cost by service, cost by
  model, token-type mix and measured savings. Aggregates only:
  workspace names, session ids and content never enter the snapshot,
  enforced by test.
- Subscription-aware money concepts: `subscriptions.json` declares the
  flat fees actually paid; the observatory separates real outlay,
  showback usage value and counterfactuals, and leads with
  **subscription leverage** (usage value ÷ outlay over the same window).

### Changed

- **Point-in-time list rates** (ADR 0008, supersedes 0007): each call
  is valued at the rate in force on its UTC day, in cost, the FOCUS
  export and the recommendations. The snapshot keeps a `history` per
  model, rebuilt from its own git history (40 moves across 38 models
  since 2026-07-06) and extended by the price-watch bot on the day it
  sees a move; the curated card has `RATE_CARD_HISTORY`. A price move
  no longer revalues the past. The maintainer's figures are unchanged:
  none of the models in use had moved.
- FOCUS export about 40% faster (100k messages: 9.3 s → 5.6 s): the
  columns shared by a record's rows are built once, and rows are
  written with `csv.writer` and `itemgetter` instead of
  `csv.DictWriter`. Output is byte-identical (golden test), and a
  property test checks every row has exactly the FOCUS columns.
- The price-watch bot says what moved. Its commit subject counts
  rate moves, models added, retired upstream or back ("chore(prices):
  1 rate move, 2 models added") and the body lists them; it used to
  title every refresh "rates changed upstream", though 17 of 28 only
  added or removed models. Rate moves now include cache read and
  write rates, retirements show on the price card, and the bot
  regenerates the card after each refresh (it had been frozen since
  July), from a full-history checkout. README and page no longer
  claim the bot commits only on rate moves.
- Right-sizing knows the current Claude lineup: Fable 5.1 → Opus 5.5,
  Opus 5.5 → Sonnet 5.5, Sonnet 5.5 → Haiku 4.5 and Opus 5 → Sonnet 5
  join the curated pairs, so the heaviest current usage gets a what-if.
- The curated Anthropic card mirrors Anthropic's pricing page as of
  2026-10-02 (was 2026-07-06): adds Claude Opus 5.5, Fable 5.1,
  Mythos 5 and 5.1, Opus 5, Sonnet 5.5 and the retired Opus 4,
  Sonnet 4 and Haiku 3.5, keyed so their dated ids resolve. Cache
  reads follow each model's published multiplier: 0.05x on Opus 5.5,
  0.025x on Fable 5.1 and Mythos 5.1, 0.1x elsewhere. Opus 5.5 was
  the maintainer's largest Claude cost and was priced only by the
  community snapshot; the values are unchanged, now from the source.
- `UsageRecord` moved to its own module, `tokencur.records`: pricing,
  the FOCUS normalizer, the ledger and every ingester now depend on a
  shared type instead of on the Claude Code ingester. The old import
  path `tokencur.ingest.claude_code.UsageRecord` still works (same
  class, pinned by a test).
- Honest labeling throughout: the report prints `API-EQUIVALENT TOTAL
  (showback)`, and the observatory/dashboard say "usage value", "avoided
  by provider caching (counterfactual)" and "right-sizing headroom" —
  none of these are money spent or saved under flat subscriptions.
- The recommend CLI speaks the same language: `AVOIDED` / `HEADROOM`
  section headers, `Total what-if headroom`, and a closing
  flat-subscription caveat (was "measured savings" / "Total potential
  savings").
- `DEFAULT_SOURCES` moved from `tokencur.report` to `tokencur.sources`,
  whose `load_records()` is now the single loader behind every command
  (five copies of the scan loop removed).
- The version has one source, `tokencur.__version__`, which
  `pyproject.toml` reads dynamically (`__init__` had drifted to 0.1.0
  while the package shipped 0.2.0).
- CI conformance gate pinned to `focus-validator` 2.2.1 (same result:
  136 rules pass).
- README states what is ingested today (three coding agents) instead
  of implying Gemini or local-model ingestion, adds an architecture
  diagram, updates pricing coverage (290+ live models, daily refresh,
  retired models kept) and explains why the target is FOCUS 1.2.

### Fixed

- Claude Code subagent transcripts, nested under
  `<project>/<session>/subagents/`, were attributed to a workspace
  called `subagents`. They now belong to their project (the first
  directory under the logs root); the ledger corrects stored rows on
  the next scan.
- Codex calls copied into a forked session are counted once. The scan
  skips a call whose timestamp and raw usage it already saw in an
  earlier rollout, and the ledger does the same against what it
  stores, so a copy is still recognized after the original log is
  deleted. This was the review's "no cross-file dedup yet".
- A malformed or corrupted log could crash a whole scan. Fuzz tests
  found it in all three ingesters: a JSON line that is not an object,
  invalid UTF-8 bytes, text where a count belongs, an out-of-range
  Kimi timestamp. Others let wrong types into records (`True` or
  `1.0` as a token count). Fields are now read through defensive
  helpers (`tokencur.ingest.fields`): a malformed usage line is
  skipped, never guessed at, and a bad byte spoils one line, not the
  scan. Real logs parse byte-identically.
- `pip install -e .[dev]` in the docs and workflows is now quoted
  (`".[dev]"`): unquoted, it is a shell glob, and zsh, the macOS
  default shell, rejects it with "no matches found".
- A Claude Code message streamed over several log lines kept its
  first line's counts, which can hold a partial output count. Each
  token count is now the largest across the message's lines (streamed
  counts only grow); the first line still names session and
  workspace. On the maintainer's logs: 1 of 2,641 messages, +1,624
  output tokens. The ledger corrects itself on the next scan, since
  record ids do not change.
- The dashboard test read the real logs of whoever ran it and found
  the app by a relative path, which newer Streamlit resolves against
  the test file; it had never run in CI. It now renders synthetic
  logs with known prices, checks the headline value, and locates the
  script through the import system (passes on Streamlit 1.59 and
  1.64).
- Claude Sonnet 5 was valued at $3/$15 per MTok. Its $2/$10 launch
  price, announced as introductory through 2026-08-31, became the
  standard price and the rise to $3/$15 was cancelled. The community
  snapshot already said $2/$10; a new test now fails whenever the
  curated card and the snapshot disagree on a model both price.
- The report's table sized its columns by hand, so billion-token
  cache totals and long model ids ran into the next column. Columns
  now fit their widest cell. Rows are counted as "model calls"
  (was "assistant messages", which only fit Claude Code).
- **Codex usage was counted about twice.** Codex re-sends each
  `token_count` report under a new timestamp (alongside rate-limit
  updates), and tokencur counted every one. A call now counts only
  when the session's running total moves; reports with no billable
  tokens are skipped. On the maintainer's logs Codex drops from 16,130
  to 8,031 calls and from $685.66 to $332.95 of API-equivalent value,
  and all 94 rollouts now reconcile exactly with Codex's own running
  total (a new test pins that reconciliation). Ledger schema 2 retires
  the re-sends already stored: the upgrade backs the file up first,
  and retired rows move to a `superseded` table with the reason, never
  deleted. Every published figure that included Codex overstated it.
- Undated usage is never given a date. A record with no timestamp
  was exported to FOCUS on 1970-01-01, which would also stretch the
  observatory's daily chart back to 1970, and a malformed timestamp
  crashed the whole export. Both are now skipped by the FOCUS
  normalizer and counted on stderr, like unpriced usage; the report
  keeps their cost in the total under an `undated` day.
- `recommendations()` walked its input twice, so a generator (any
  one-shot iterable) silently lost every right-sizing result. It now
  materializes the records once; a regression test feeds it a
  generator.
- Pricing snapshot no longer loses models that LiteLLM prunes upstream.
  Retired models keep their last known rate, flagged
  `retired_upstream`, so historical usage stays priced; 56 models
  dropped by earlier daily refreshes were restored from git history.
  Before this, `main` had two failing tests while the badge stayed
  green: the price-watch bot pushes with `GITHUB_TOKEN`, which does not
  trigger CI. The price-watch job now runs the test suite before it
  commits.
- Claude Code's synthetic placeholder messages (model `<synthetic>`,
  all-zero usage — client-side stubs for API errors and interrupted
  turns) are skipped at parse time. They are not API traffic and were
  surfacing as noise in the unpriced-usage section of reports.

## [0.2.0] - 2026-07-13

### Added

- Codex CLI ingester: parses rollout `token_count` events, splitting
  OpenAI's cached input out of `input_tokens` (no write premium).
- Kimi Code ingester: parses per-turn `usage.record` wire-log lines.
- Multi-source report: with no arguments, every known local source is
  scanned (Claude Code, Codex, Kimi Code) with per-source totals.
- Vendor prefixes (`moonshot-ai/…`) are stripped when resolving rates.
- Vendored snapshot of the LiteLLM community price database as a
  fallback pricing layer (284 models: Anthropic, OpenAI, Gemini,
  DeepSeek, Kimi/Moonshot, GLM/Z.ai, Ollama), with a deliberate
  refresh script. The curated card still wins; cache rates come from
  explicit per-model fields since multipliers differ across providers.
- FOCUS 1.2 normalizer and `python -m tokencur.export`: one charge row
  per token bucket, showback cost semantics, explicit-null InvoiceId.
- CI conformance gate: the export must pass the FinOps Foundation's
  own `focus-validator` (spec 1.2) on every push.
- Documented proxy rate for Kimi Code's `kimi-k2.7-code-highspeed`
  alias (kimi-k2.6 list rates), so real usage no longer reads as
  unpriced.
- Cross-tests against the FinOps Foundation's official sample data
  (CC BY 4.0 fixture slice): shared core vocabulary, mutually
  parseable datetimes (ours strict ISO-8601 Z), spec-bounded
  ChargeCategory, and cost/currency conventions.
- Daily price-watch workflow: refreshes the LiteLLM snapshot and
  commits only on real rate changes.
- Streamlit + DuckDB dashboard (`pip install -e .[dashboard]`): KPI
  tiles, daily cost by service, cost by model, token-type mix and
  unit economics — every view exposing its SQL and table.
- RunPod billing-API probe script (auth recipe + dailyCharges shape),
  groundwork for the billed-cost ingester.
- Recommendation engine (`python -m tokencur recommend` and a
  dashboard section): measured caching ROI per model (savings vs
  re-sending cached tokens as fresh input) and what-if model
  right-sizing over curated one-tier-down pairs, emitted only when
  the sibling is actually cheaper at list rates.
- `python -m tokencur <report|export|recommend>` command router.

## [0.1.0] - 2026-07-06

### Added

- Claude Code JSONL ingestion: one usage record per assistant message,
  deduplicated across streamed lines and resumed sessions. Reads usage
  metadata only — never conversation content.
- Versioned Anthropic rate card at public list prices, with cache-tier
  economics (reads 0.1x input; writes 1.25x for 5m TTL, 2x for 1h TTL).
  Unknown models surface as unpriced usage instead of costing $0.
- `python -m tokencur.report`: per-model and per-day API-equivalent
  list cost over local Claude Code logs. Stdlib only.
