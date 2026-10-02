"""Refresh the vendored LiteLLM pricing snapshot.

Downloads the community-maintained price database, keeps only the
providers tokencur ingests, and writes a small pinned snapshot into the
package. Run deliberately; commit the diff so pricing changes are
reviewable, reproducible and offline.

Usage:
    python scripts/update_pricing_snapshot.py [--commit-message FILE]
    python scripts/update_pricing_snapshot.py --backfill-history

With ``--commit-message``, a refresh that changes the snapshot also
writes the commit message for it, naming what moved (rate moves, models
added, retired upstream...); the price-watch action commits with it.

Each model keeps the rates it had before a move in its ``history``, so
a call is valued at the rate in force on its day. ``--backfill-history``
rebuilds that history from the snapshot's own git history (no download).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tokencur.prices import commit_message

SOURCE = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)
# moonshot = Kimi, zai = GLM (Zhipu), ollama = local models ($0 rates,
# used by the local-vs-API break-even analysis).
PROVIDERS = {
    "anthropic",
    "openai",
    "gemini",
    "deepseek",
    "moonshot",
    "zai",
    "ollama",
}
#: The per-token rates of an entry: what a rate move changes and history keeps.
RATE_FIELDS = (
    "input_cost_per_token",
    "output_cost_per_token",
    "cache_read_input_token_cost",
    "cache_creation_input_token_cost",
    "cache_creation_input_token_cost_above_1hr",
)
FIELDS = (*RATE_FIELDS, "litellm_provider")
REPO = Path(__file__).parent.parent
SNAPSHOT_REL = "src/tokencur/pricing_data/litellm_snapshot.json"
TARGET = REPO / SNAPSHOT_REL


def build_snapshot(
    full: dict, previous: dict | None, today: str | None = None
) -> dict[str, dict]:
    """Filter the upstream database down to tokencur's providers.

    Models that upstream drops (LiteLLM prunes retired ones) are kept
    at their last known rate and flagged ``retired_upstream``: old
    usage logs still name them, and losing the rate would silently turn
    historical spend into unpriced usage.

    When a model's rates move, the rates it had go into its ``history``
    with ``until`` = ``today``, the day the move was seen (the daily
    price-watch run makes that at most a day late). Pure — no IO, and
    ``today`` defaults to the current date — so it carries the test weight.
    """
    today = today or date.today().isoformat()
    previous = previous or {}
    snapshot: dict[str, dict] = {}
    for key in sorted(full):
        entry = full[key]
        if not isinstance(entry, dict):
            continue
        if entry.get("litellm_provider") not in PROVIDERS:
            continue
        if "input_cost_per_token" not in entry or "output_cost_per_token" not in entry:
            continue
        # Keys sometimes carry a "provider/" prefix; store the bare name.
        bare = key.split("/", 1)[-1]
        if bare in snapshot:
            continue  # first (sorted) entry wins, deterministically
        snapshot[bare] = {f: entry[f] for f in FIELDS if f in entry}

    for name, entry in snapshot.items():
        old = previous.get(name)
        if old is None:
            continue
        history = list(old.get("history", []))
        if _rates(old) != _rates(entry):
            _keep(history, today, _rates(old))
        if history:
            entry["history"] = history

    for name, entry in previous.items():
        if name not in snapshot:
            snapshot[name] = {**entry, "retired_upstream": True}
    return dict(sorted(snapshot.items()))


def rate_history(versions: list[tuple[str, dict]]) -> dict[str, list[dict]]:
    """Each model's past rates, rebuilt from dated snapshot versions.

    ``versions`` are (day, models) pairs, oldest first, as the snapshot's
    git history holds them. Whenever a model's rates differ from the last
    version that listed it, the earlier rates go into its history with
    ``until`` = the day of the version that changed them. Pure; the git
    walk stays in ``main``.
    """
    history: dict[str, list[dict]] = {}
    last: dict[str, dict] = {}
    for day, models in versions:
        for name, entry in models.items():
            rates = _rates(entry)
            before = last.get(name)
            if before is not None and before != rates:
                _keep(history.setdefault(name, []), day, before)
            last[name] = rates
    return history


def _rates(entry: dict) -> dict:
    return {f: entry[f] for f in RATE_FIELDS if f in entry}


def _keep(history: list[dict], until: str, rates: dict) -> None:
    """Append rates that applied before ``until``. Two moves on one day
    keep the rate from before that day: no call can be dated between them."""
    if not (history and history[-1]["until"] == until):
        history.append({"until": until, **rates})


def _write(models: dict, fetched: str) -> None:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(
        json.dumps(
            {"_meta": {"source": SOURCE, "fetched": fetched}, "models": models},
            indent=1,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(REPO), *args], capture_output=True, text=True, check=True
    ).stdout


def backfill_history() -> None:
    """Rebuild every model's history from the snapshot's git history."""
    versions = []
    log = _git("log", "--reverse", "--format=%H %cs", "--", SNAPSHOT_REL)
    for line in log.splitlines():
        sha, day = line.split()
        models = json.loads(_git("show", f"{sha}:{SNAPSHOT_REL}")).get("models", {})
        versions.append((day, models))
    current = json.loads(TARGET.read_text(encoding="utf-8"))
    history = rate_history(versions)
    for name, entry in current["models"].items():
        entry.pop("history", None)
        if name in history:
            entry["history"] = history[name]
    _write(current["models"], current.get("_meta", {}).get("fetched", "unknown"))
    moves = sum(len(h) for h in history.values())
    print(
        f"rate history: {moves} moves for {len(history)} models, "
        f"from {len(versions)} snapshot versions"
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--commit-message",
        type=Path,
        metavar="FILE",
        help="on a change, write the commit message describing it here",
    )
    parser.add_argument(
        "--backfill-history",
        action="store_true",
        help="rebuild rate history from the snapshot's git history (no download)",
    )
    args = parser.parse_args(argv)
    if args.backfill_history:
        backfill_history()
        return

    with urllib.request.urlopen(SOURCE, timeout=60) as resp:
        full = json.load(resp)

    previous = None
    if TARGET.exists():
        previous = json.loads(TARGET.read_text(encoding="utf-8")).get("models")
    snapshot = build_snapshot(full, previous)

    # Only touch the file when rates actually changed, so automated
    # refreshes produce commits with meaning (a dated price-change log),
    # not daily noise from the fetched-at stamp.
    if previous == snapshot:
        print(f"no price changes; snapshot untouched ({len(snapshot)} models)")
        return

    _write(snapshot, date.today().isoformat())
    print(f"wrote {len(snapshot)} models to {TARGET}")
    message = commit_message(previous or {}, snapshot)
    print(message)
    if args.commit_message:
        args.commit_message.write_text(message, encoding="utf-8")


if __name__ == "__main__":
    main()
