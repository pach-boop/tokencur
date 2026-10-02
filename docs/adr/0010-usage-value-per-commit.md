# 0010. Usage value per commit: attribute each call by where it ran

- Status: Accepted
- Date: 2026-10-02

## Context

tokencur measured cost and nothing else. An external review put the gap
plainly: a cheaper model is not a better choice until cost is set
against what the work produced, and "cost + quality + latency +
reliability" is what unit economics means. The first step is a unit:
something the usage went into, counted the same way everywhere.

Two things were missing. A unit: commits are the one output every
repository already records, locally, with dates and authors. And an
attribution: which calls went into which repository. The `workspace`
field names the directory a session started in, which is wrong whenever
the agent moves: on the maintainer's logs, 71% of Claude Code calls ran
outside their session's starting directory. But the logs say where each
call ran. Claude Code writes `cwd` on every line and updates it as the
agent changes directory; Codex writes it in `session_meta` and each
`turn_context`; Kimi Code in each session's `state.json`, one directory
per `wd_` folder.

## Decision

- Each record keeps the directory its call ran in (`UsageRecord.cwd`,
  ledger schema 5). It stays local: never in the FOCUS export, never in
  the observatory. A rescan fills it for rows whose logs are still on
  disk, and a parse that cannot see it never erases a known one.
- A call belongs to the git repository holding that directory: the
  nearest directory going up with a `.git` entry, as git itself decides.
  A call is never split across repositories. Calls with no directory
  logged, in a directory that no longer exists, or outside any
  repository stay unattributed, and the output says how much.
- The unit is the commit: non-merge commits on the checked-out branch,
  by the repository's `user.email` unless `--all-authors`, authored on a
  UTC day in the window.
- The window is the period asked for, or else each repository's days
  with agent usage. Commits outside those days had no recorded usage, so
  they have no known cost.
- Commits signed by an agent (author or `Co-authored-by` trailer, by
  unambiguous signatures only) and lines changed are shown beside the
  count as context, never as the unit.
- The value is showback, API-equivalent list value, labeled as such.

## Consequences

- `tokencur outcomes` answers "what did the agents' usage cost per commit,
  repository by repository", with no configuration and nothing sent
  anywhere: git runs read-only on local repositories. On 2026-10-02,
  tokencur's own repository showed $70.78 of usage value over 40 commits:
  $1.77 per commit.
- A commit is a coarse unit. It says nothing about size, difficulty or
  quality, and it can be gamed. The command says so in its output, and
  the useful comparison is a repository with itself over time.
- Agent signatures undercount: Codex CLI and Kimi Code sign no commits
  by default. The count measures signatures, not involvement.
- Commits on other branches are not counted until they reach the
  checked-out one. That avoids counting a rebased commit twice.
- Quality, latency and reliability remain to be added. Candidate signals
  are reverts, test runs and retried or failed calls in the logs.
  Merged pull requests would need a forge's API, which tokencur leaves
  for later because it makes no network calls at runtime.
