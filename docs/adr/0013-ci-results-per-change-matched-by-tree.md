# 0013. CI results per change, from a capture, matched by tree

- Status: Accepted
- Date: 2026-10-05

## Context

ADR 0012 counts a change as a success when it reaches the default branch
and is never reverted. That says nothing about quality: a change can
land with its tests failing. tokencur's own main branch requires no
status checks, and four changes whose own CI failed landed there and
count as successes.

Agent logs cannot say whether tests passed. Reading test output would
need message content, which ADR 0006 rules out, and no agent logs a
structured exit code. The forge's CI does know: GitHub Actions lists
every run of a workflow, with the commit it checked out and how it
ended. Asking it is a network call, which tokencur itself makes nowhere
(ADRs 0009 and 0010).

Measured on tokencur's repository on 2026-10-03, three facts shaped how
runs are matched to changes:

- Pull requests were rebased onto main, which gives each change a new
  commit hash. Looked up by commit hash, 11 of the last 60 changes on
  main have a run. Looked up by tree hash, 29 do: a rebase onto a base
  that has not moved keeps the tree, the exact code.
- The workflow runs on pushes to main and on pull requests, and a push
  tests only its newest commit. About half of the changes are never
  tested on their own code.
- Calling a change green because the first later commit tested on main
  passed would call all 60 green. It would hide the 4 that failed,
  because the green commit after them is the one that fixed them.

The maintainer agreed on 2026-10-03 that CI results go in the ledger
and that the headline stays, with a CI figure next to it. On 2026-10-05
they approved the rules below.

## Decision

- **Capture outside the package.** `scripts/fetch_github_ci.py` lists
  one named workflow's runs, one call per 100 runs. It writes only each
  run's id, attempt, event, status and conclusion, the commit it checked
  out, that commit's tree hash, and the run's times. It writes no commit
  message, name, email, branch or title, and it never fetches a job log.
  The workflow must be named, because a repository's runs include Pages,
  CodeQL and scheduled jobs, whose success says nothing about the code.
  A token is optional, since public repositories need none. When one is
  set, it goes only to api.github.com, in a header that is dropped on
  any redirect, and it is never written.
- **Import offline.** `tokencur import github-ci FILE` keeps the runs in
  a `ci_runs` table (ledger schema 6), keyed on repository, run and
  attempt. This is the first time the ledger stores identifiers from a
  forge: the repository's owner/name, and commit and tree hashes. They
  stay in the local ledger, never in the export or the observatory.
- **Match by tree.** A run tested a change's own code when the commit it
  checked out has the same tree as the change's copy on the default
  branch. A repository's runs are the ones whose owner/name matches
  where its `origin` points. tokencur reads that URL only when the
  ledger holds CI runs.
- **Judge each change that landed and stayed:**
  - *passed*: CI tested its own code, and every run that did passed;
  - *failed*: some run on its own code failed;
  - *tested only with later commits*: CI never tested its own code, only
    later commits on the default branch that contain it. It inherits no
    result from them;
  - *never tested*.

  A run counts by its latest attempt, so a re-run that passed replaces
  the attempt that failed. A run that was cancelled or skipped, that
  could not start because its workflow file was broken, or that had not
  finished is no result.
- **Report next to the headline, never instead of it.** The figure is
  usage value per change that also passed its own CI, over the usage in
  repositories with CI captured. It is always followed by how many
  changes CI tested that way. The line that names what reproduces the
  figures also names the capture.

## Consequences

- On tokencur's own repository on 2026-10-05, 61 changes landed and
  stayed. CI tested the code of 30 of them: 26 passed and 4 failed. The
  other 31 were tested only with later commits. $192.30 of usage value
  comes to $3.15 per successful change, and to $7.40 per change that
  passed its own CI. The second figure is higher because it divides by
  fewer changes, not because the work cost more.
- Coverage is partial by design, because a push tests only its newest
  commit. The figure always says how much it covers, and should be read
  with that number.
- A failed run may have failed the linter, not a test, because a run's
  conclusion covers all of its jobs. Telling them apart takes one more
  call per run, and is left for later.
- A pull request's run tests the merge of the branch into its base, but
  the run records the branch's own commit. When the branch is behind
  its base, that merge holds code the branch does not. After a rebase or
  a squash, the code that lands then differs from the branch, so no run
  matches and the change gets no result of its own: matching misses a
  test rather than crediting one. A merge commit lands the branch's own
  commit as it is, so there a run on a branch that was behind its base
  counts for it, although the run also tested the base's newer code.
- Only GitHub Actions is read, one workflow per capture. GitLab and other
  forges report no tree hash, so they would have to match by commit
  hash. They are left until someone needs them.
- Runs kept in the ledger no longer depend on GitHub keeping them. The
  results are as of the capture; running the script again refreshes
  them.
