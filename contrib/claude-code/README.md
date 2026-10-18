# Claude Code status line and hooks

Small commands that put tokencur's numbers where a Claude Code session can see
them. None of them calls a model, so they cost no tokens. They live in one
file, `tokencur_hooks.py`, standard library only, and read
[`tokencur limits --json`](../../README.md#plan-limits-how-much-of-a-subscription-is-left)
through a cache.

| Command | Wired as | What it does |
| --- | --- | --- |
| `statusline` | status line | This session's context against a cut (green, yellow from 250k, red from 300k), then the plan limits: `ctx 216k │ week 68% · 5h 15%` |
| `session-start` | `SessionStart` hook | Logs the start and tells the new session how close the limits are, when the week's pace reaches its limit, and how to measure its own context |
| `session-end` | `SessionEnd` hook | Logs the end |
| `context ID` | run by hand, or by the agent | A session's real context, from its transcript (`ID` can be a prefix) |
| `refresh` | started by the hooks | Runs `tokencur limits --json` into the cache, one at a time, at the lowest priority |
| `watch` | systemd timer | A desktop notice once per step a limit crosses: week 85 % and 95 %, five hours 65 % and 85 % |

## Why

- **Context drives cost.** Every call rereads the whole context, so a long
  session costs more per call the longer it runs. A cut (300k tokens by
  default) is a point to start a new session at the next natural break.
- **An agent's own estimate of its context runs low.** It does not see the
  system prompt or the tool definitions. On the maintainer's sessions the gap
  was 50k to 110k tokens. `context` reads what the API reported on the last
  call instead, and the session-start notice gives each session the command
  with its own id.
- **Several sessions share one plan.** The cache lets every status line and
  every new session show the same limits without each running tokencur, which
  takes a few seconds on a large history.

## Install

1. Install tokencur (`pip install tokencur`) and clone this repository; the
   examples assume `~/src/tokencur`. Check that `tokencur limits --json` works.
2. Add the status line and the two hooks to `~/.claude/settings.json`, merging
   them with what is there already: `settings.example.json` has the blocks.
   Use the full path to the clone.
3. Optional, Linux with systemd: the limits watch.

   ```sh
   cp contrib/claude-code/systemd/tokencur-limits-watch.* ~/.config/systemd/user/
   # edit ExecStart (and TOKENCUR_BIN if tokencur is not on the system PATH)
   systemctl --user daemon-reload
   systemctl --user enable --now tokencur-limits-watch.timer
   ```

   It does nothing while no `claude` process runs, and needs `notify-send`.

New sessions pick the hooks up; the first one starts the first measurement.

## Settings

All optional, from the environment of Claude Code (or of the systemd unit):

| Variable | Default | |
| --- | --- | --- |
| `TOKENCUR_BIN` | `tokencur` | the tokencur command |
| `TOKENCUR_PLANS` | tokencur's own default | a `subscriptions.json` for `--plans` |
| `TOKENCUR_STATE_DIR` | `$XDG_STATE_HOME/tokencur-hooks` | the cache `limits.json`, the session log `sessions.tsv`, the watch marks |
| `TOKENCUR_CONTEXT_CUT` | `300000` | context past which to start a new session |
| `TOKENCUR_CONTEXT_WARN` | `250000` | context at which the status line turns yellow |
| `TOKENCUR_WATCH_WEEK` | `85,95` | weekly steps, in percent |
| `TOKENCUR_WATCH_5H` | `65,85` | five-hour steps, in percent |

## Limits

- The percent is what `tokencur limits` measures: by Claude Code's own meter
  when it was read in the current window, else against the lowest ceiling hit
  on the plan. It is a gauge, not a balance.
- The status line shows the cache's age once it is an hour old: the cache is
  refreshed when a session opens and, with the timer, every 20 minutes.
- The watch is Linux-only (`pgrep`, `notify-send`, systemd). The status line
  and the hooks run wherever Python 3.11 does; on Windows two refreshes can
  overlap, which costs time but no harm.
