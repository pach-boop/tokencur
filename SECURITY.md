# Security policy

## What tokencur touches

- **Reads**, read-only, the local logs of coding agents
  (`~/.claude/projects`, `~/.codex/sessions`, `~/.kimi-code/sessions`) and
  only their usage metadata: token counts, model ids, timestamps,
  workspace and session ids, the request and message ids that keep a call
  from counting twice, the agent's version, the request options that
  change a call's price, the directory each call ran in (for Kimi Code,
  the `cwd` field of each session's `state.json`), and the cost and
  API-time counters Claude Code writes when a session ends, with the
  session each one continued in. Log lines and Kimi Code's `state.json`
  files are decoded as JSON to reach those fields; message content is
  never read into a record, stored or logged. A test asserts that none of
  it reaches the public observatory output, and working directories stay
  in the ledger: never in the FOCUS export or the observatory.
- **Runs git**, read-only, for `tokencur outcomes`: `git log`,
  `git rev-list`, `git patch-id`, `git rev-parse`, `git symbolic-ref` and
  `git config user.email` in the repositories the agents worked in. From
  each commit it reads the hash, the author's name, email and date, the
  `Co-authored-by` trailers and the lines changed, to count commits,
  agent signatures and lines; the whole message only to find
  `This reverts commit <sha>`; and the diff only as input to
  `git patch-id`, to recognise a rebased copy. Nothing from a repository
  is stored or published. `tokencur prices` also runs `git log` and
  `git show` in the current directory, on the pricing snapshot's history
  in a tokencur checkout. tokencur never runs `git fetch`, and
  `outcomes` runs git with `GIT_NO_LAZY_FETCH`: with git 2.45 or newer, a
  partial clone is reported as unreadable rather than left to download
  the objects it is missing.
- **Message origin**: for `outcomes --sessions`, and whenever `doctor`
  reads Claude Code's counters, whether a person typed each Claude Code
  message: the line's sidechain, meta and compaction flags, the origin
  Claude Code records or, on lines from before it recorded one, whether
  the content is plain text and the types of its blocks. Never the text.
- **Writes** the ledger at `~/.local/share/tokencur/ledger.sqlite3`
  (honours `$XDG_DATA_HOME`, or `$TOKENCUR_LEDGER`), created readable by
  its owner only (`0600`, directory `0700`). Before a schema upgrade it
  copies the ledger next to itself as `<name>.schema-<N>.bak`, also
  `0600`. `export` writes the CSV you name, and `observatory` and
  `prices` write their pages where you point them (by default
  `docs/observatory` and `docs/prices` under the current directory).
- **Network**: none at runtime, with one exception: in a partial clone,
  git downloads the past versions of the pricing snapshot that
  `tokencur prices` asks for. Two scripts go online when you run them:
  `scripts/update_pricing_snapshot.py` downloads the LiteLLM price database
  (also run by the daily price-watch action), and
  `scripts/fetch_runpod_billing.py` downloads your RunPod billing history,
  sending the API key only in the Authorization header and never writing it.
- **Billing exports** saved by that script stay in
  `~/.local/share/tokencur/raw/`, readable by their owner only.
  `tokencur import runpod FILE` keeps from each row the pod id, the
  billing period, the amount, the GPU time and the disk billed, in the
  ledger. The published pages make no external requests.
- **Dependencies**: none at runtime. Optional extras and dev tools are
  tracked by Dependabot; GitHub Actions are pinned to commit SHAs and run
  with least-privilege tokens.

The main data-integrity risk is a wrong rate in the vendored price
snapshot, which is third-party, community-maintained data. Mitigations: a
curated card that wins over the snapshot, retention of retired models at
their last known rate, and the test suite running before any refresh is
committed.

## Verifying a release

Release files are built by the release workflow, which signs a build
provenance attestation for each one. Check that a file you downloaded was
built from this repository:

```bash
gh attestation verify tokencur-X.Y.Z-py3-none-any.whl --repo pach-boop/tokencur
```

The pricing snapshot every cost is computed from is published the same way,
as `tokencur-pricing-snapshot-X.Y.Z.json` with its own attestation:

```bash
gh attestation verify tokencur-pricing-snapshot-X.Y.Z.json --repo pach-boop/tokencur
sha256sum tokencur-pricing-snapshot-X.Y.Z.json   # equals what `tokencur doctor` prints
```

## Supported versions

The latest release and `main`. Older versions are not patched.

## Reporting a vulnerability

Report privately through GitHub's private vulnerability reporting:
<https://github.com/pach-boop/tokencur/security/advisories/new>

Do not open a public issue for a security problem. You will get an
acknowledgement within 7 days and a fix or a clear answer within 30.
Credit goes in the advisory and the changelog unless you prefer otherwise.
