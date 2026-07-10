"""Refresh the vendored LiteLLM pricing snapshot.

Downloads the community-maintained price database, keeps only the
providers tokencur ingests, and writes a small pinned snapshot into the
package. Run deliberately; commit the diff so pricing changes are
reviewable, reproducible and offline.

Usage:
    python scripts/update_pricing_snapshot.py
"""

from __future__ import annotations

import json
import urllib.request
from datetime import date
from pathlib import Path

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
FIELDS = (
    "input_cost_per_token",
    "output_cost_per_token",
    "cache_read_input_token_cost",
    "cache_creation_input_token_cost",
    "cache_creation_input_token_cost_above_1hr",
    "litellm_provider",
)
TARGET = Path(__file__).parent.parent / "src/tokencur/pricing_data/litellm_snapshot.json"


def main() -> None:
    with urllib.request.urlopen(SOURCE, timeout=60) as resp:
        full = json.load(resp)

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

    # Only touch the file when rates actually changed, so automated
    # refreshes produce commits with meaning (a dated price-change log),
    # not daily noise from the fetched-at stamp.
    if TARGET.exists():
        previous = json.loads(TARGET.read_text(encoding="utf-8")).get("models")
        if previous == snapshot:
            print(f"no price changes; snapshot untouched ({len(snapshot)} models)")
            return

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(
        json.dumps(
            {"_meta": {"source": SOURCE, "fetched": date.today().isoformat()},
             "models": snapshot},
            indent=1,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(snapshot)} models to {TARGET}")


if __name__ == "__main__":
    main()
