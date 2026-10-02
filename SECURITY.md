# Security policy

## What tokencur touches

- **Reads**, read-only, the local logs of coding agents
  (`~/.claude/projects`, `~/.codex/sessions`, `~/.kimi-code/sessions`) and
  only their usage metadata: token counts, model ids, timestamps,
  workspace and session ids. Message content is never parsed, stored or
  logged. A test asserts that none of it reaches the public observatory
  output.
- **Writes** one file: the ledger at `~/.local/share/tokencur/ledger.sqlite3`
  (honours `$XDG_DATA_HOME`, or `$TOKENCUR_LEDGER`), created readable by
  its owner only (`0600`, directory `0700`).
- **Network**: none at runtime. The one network call in the repository is
  `scripts/update_pricing_snapshot.py`, which downloads the LiteLLM price
  database when run deliberately or by the daily price-watch action. The
  published pages make no external requests.
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
