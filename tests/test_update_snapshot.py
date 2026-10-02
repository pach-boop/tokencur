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
    snapshot = update.build_snapshot(
        {"moonshot/kimi-k3": _entry(cost=2e-6)}, previous, today="2026-10-03"
    )

    entry = snapshot["kimi-k3"]
    assert "retired_upstream" not in entry
    assert entry["input_cost_per_token"] == 2e-6
    # The rate it came back at differs, so the old one is history.
    assert entry["history"] == [
        {
            "until": "2026-10-03",
            "input_cost_per_token": 1e-6,
            "output_cost_per_token": 4e-6,
        }
    ]


def test_other_providers_are_filtered_out():
    snapshot = update.build_snapshot({"bedrock/x": _entry(provider="bedrock")}, None)

    assert snapshot == {}


def test_pruned_kimi_preview_stays_priced():
    # Regression: an upstream prune once removed this id and broke
    # Kimi Code pricing for historical logs.
    from tokencur.pricing import rates_for

    assert rates_for("moonshot-ai/kimi-k2-0711-preview") is not None


def test_a_rate_move_keeps_the_old_rate_with_the_day_it_stopped():
    previous = {"m": _entry(provider="openai", cost=1e-6)}
    moved = update.build_snapshot(
        {"m": _entry(provider="openai", cost=1.5e-6)}, previous, today="2026-09-17"
    )
    again = update.build_snapshot(
        {"m": _entry(provider="openai", cost=1.25e-6)}, moved, today="2026-10-01"
    )

    assert again["m"]["input_cost_per_token"] == 1.25e-6
    assert [h["until"] for h in again["m"]["history"]] == ["2026-09-17", "2026-10-01"]
    assert [h["input_cost_per_token"] for h in again["m"]["history"]] == [1e-6, 1.5e-6]


def test_unchanged_rates_add_no_history():
    previous = {"m": _entry(provider="openai")}

    snapshot = update.build_snapshot(
        {"m": _entry(provider="openai")}, previous, "2026-10-01"
    )

    assert "history" not in snapshot["m"]
    assert snapshot == {"m": _entry(provider="openai")}


def test_history_is_rebuilt_from_dated_snapshot_versions():
    r1, r2, r3 = (_entry(provider="openai", cost=c) for c in (1e-6, 2e-6, 3e-6))
    versions = [
        ("2026-07-06", {"m": r1, "steady": r1}),
        ("2026-07-10", {"m": r2, "steady": r1}),
        ("2026-08-01", {"m": r2, "steady": r1}),
        ("2026-09-01", {"m": r3, "steady": r1, "late": r1}),
    ]

    history = update.rate_history(versions)

    assert [(h["until"], h["input_cost_per_token"]) for h in history["m"]] == [
        ("2026-07-10", 1e-6),
        ("2026-09-01", 2e-6),
    ]
    assert "steady" not in history and "late" not in history
