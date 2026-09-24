import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).parent.parent / "scripts/update_pricing_snapshot.py"
_spec = importlib.util.spec_from_file_location("update_pricing_snapshot", _SCRIPT)
update = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(update)


def _entry(provider="moonshot", cost=1e-6):
    return {
        "input_cost_per_token": cost,
        "output_cost_per_token": cost * 4,
        "litellm_provider": provider,
    }


def test_models_dropped_upstream_keep_their_last_rate():
    previous = {"kimi-k2-0711-preview": _entry()}
    snapshot = update.build_snapshot({"moonshot/kimi-k3": _entry()}, previous)

    assert snapshot["kimi-k2-0711-preview"]["input_cost_per_token"] == 1e-6
    assert snapshot["kimi-k2-0711-preview"]["retired_upstream"] is True
    assert "retired_upstream" not in snapshot["kimi-k3"]


def test_model_back_upstream_takes_the_fresh_rate():
    previous = {"kimi-k3": {**_entry(cost=1e-6), "retired_upstream": True}}
    snapshot = update.build_snapshot({"moonshot/kimi-k3": _entry(cost=2e-6)}, previous)

    assert snapshot["kimi-k3"] == _entry(cost=2e-6)


def test_other_providers_are_filtered_out():
    snapshot = update.build_snapshot({"bedrock/x": _entry(provider="bedrock")}, None)

    assert snapshot == {}


def test_pruned_kimi_preview_stays_priced():
    # Regression: an upstream prune once removed this id and broke
    # Kimi Code pricing for historical logs.
    from tokencur.pricing import rates_for

    assert rates_for("moonshot-ai/kimi-k2-0711-preview") is not None
