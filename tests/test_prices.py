import json
import subprocess
from pathlib import Path

from tokencur.prices import (
    curated_card,
    diff_models,
    price_changes,
    render_html,
    snapshot_meta,
)


def _entry(inp: float, out: float) -> dict:
    return {"input_cost_per_token": inp, "output_cost_per_token": out}


class TestDiffModels:
    def test_detects_added_and_removed(self):
        before = {"a": _entry(1e-6, 2e-6)}
        after = {"a": _entry(1e-6, 2e-6), "b": _entry(3e-6, 4e-6)}

        added, removed, changed = diff_models(before, after)

        assert added == ["b"]
        assert removed == []
        assert changed == []

    def test_reports_rate_move_in_usd_per_mtok(self):
        before = {"m": _entry(1e-6, 2e-6)}  # $1 / $2 per MTok
        after = {"m": _entry(1.5e-6, 2e-6)}  # input up to $1.50

        _, _, changed = diff_models(before, after)

        assert len(changed) == 1
        delta = changed[0]
        assert delta.model == "m" and delta.field == "input"
        assert round(delta.old, 4) == 1.0 and round(delta.new, 4) == 1.5

    def test_unchanged_rates_produce_no_deltas(self):
        card = {"m": _entry(1e-6, 2e-6)}
        assert diff_models(card, dict(card)) == ([], [], [])

    def test_missing_field_on_one_side_is_not_a_change(self):
        # A model that gains a field it never priced before must not read
        # as a rate move (old is None) — guards against phantom deltas.
        before = {"m": {"input_cost_per_token": 1e-6}}
        after = {"m": _entry(1e-6, 2e-6)}

        _, _, changed = diff_models(before, after)

        assert changed == []


def _run(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _commit(repo: Path, rel: str, models: dict, when: str) -> None:
    (repo / rel).write_text(json.dumps({"models": models}), encoding="utf-8")
    _run(repo, "add", rel)
    _run(
        repo,
        "-c", "user.name=t", "-c", "user.email=t@t",
        "commit", "-m", "snap", "--date", when,
    )


class TestPriceChanges:
    def test_walks_history_newest_first_with_deltas(self, tmp_path):
        _run(tmp_path, "init", "-b", "main")
        rel = "snap.json"
        _commit(tmp_path, rel, {"m": _entry(1e-6, 2e-6)}, "2026-07-01T00:00:00Z")
        _commit(
            tmp_path, rel,
            {"m": _entry(2e-6, 2e-6), "new": _entry(9e-6, 9e-6)},
            "2026-07-05T00:00:00Z",
        )

        changes = price_changes(tmp_path, rel)

        # Newest first: the rate move, then the introduction.
        assert len(changes) == 2
        latest = changes[0]
        assert latest.date == "2026-07-05"
        assert latest.added == ["new"]
        assert [d.field for d in latest.changed] == ["input"]
        assert round(latest.changed[0].new, 2) == 2.0
        # Root commit is flagged as an introduction, not 1 spurious add.
        assert changes[1].is_introduction

    def test_expansion_commit_is_not_an_introduction(self, tmp_path):
        # A later commit that only adds models (no rate move) is an
        # expansion, not an introduction — the exact distinction real
        # history surfaced: the price-watch bot adding a new model must
        # not read as "snapshot introduced".
        _run(tmp_path, "init", "-b", "main")
        rel = "snap.json"
        _commit(tmp_path, rel, {"m": _entry(1e-6, 2e-6)}, "2026-07-01T00:00:00Z")
        _commit(
            tmp_path, rel,
            {"m": _entry(1e-6, 2e-6), "brand-new": _entry(9e-6, 9e-6)},
            "2026-07-05T00:00:00Z",
        )

        latest, root = price_changes(tmp_path, rel)

        assert latest.added == ["brand-new"] and latest.changed == []
        assert not latest.is_introduction  # expansion, not introduction
        assert root.is_introduction  # only the true root is

    def test_no_git_repo_returns_empty(self, tmp_path):
        # Not a git repo → graceful empty, page still renders from card.
        assert price_changes(tmp_path, "snap.json") == []


def test_render_is_self_contained_and_has_no_external_scripts():
    changes = price_changes(Path("."))  # real repo history (may be empty)
    page = render_html(curated_card(), snapshot_meta(), changes)

    assert "<style>" in page and "claude-opus-4-8" in page
    # No external scripts, stylesheets or font/image loads.
    for forbidden in ('<script', 'src="http', "@import", "url(http"):
        assert forbidden not in page


def test_curated_card_is_in_mtok_units():
    rows = {r["model"]: r for r in curated_card()}
    # Opus 4.8 lists at $5 input / $25 output per MTok.
    assert rows["claude-opus-4-8"]["input"] == 5.0
    assert rows["claude-opus-4-8"]["output"] == 25.0
