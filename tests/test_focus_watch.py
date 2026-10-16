import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).parent.parent / "scripts/focus_watch.py"
_spec = importlib.util.spec_from_file_location("focus_watch", _SCRIPT)
watch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(watch)

PINNED_DIGEST = f"sha256:{watch.gate.RULES_SHA256}"
TOLERATED = sorted(watch.gate.TOLERATED)


def _asset(name, digest="sha256:0"):
    return {"name": name, "digest": digest, "browser_download_url": f"https://x/{name}"}


def _release(*names, prerelease=False, draft=False):
    return {
        "prerelease": prerelease,
        "draft": draft,
        "assets": [_asset(n) for n in names],
    }


def _report(rules, failures=(), unexpected=None, passed=None, summary="Total: 1"):
    failures = sorted(failures)
    unexpected = failures if unexpected is None else unexpected
    return {
        "rules": rules,
        "summary": summary,
        "failures": failures,
        "unexpected": unexpected,
        "passed": not unexpected if passed is None else passed,
        "last_line": "ValueError: Active-edge cycle detected; blocked nodes: [...]",
    }


# Today's FOCUS_Spec releases, newest first, as the GitHub API lists them.
RELEASES = [
    _release("FOCUS_spec-v1_4.pdf", "model-1.4.json"),
    _release(
        "model-1.3.0.1.json",
        "model-1.3.0.4.json",
        "model-1.4.0.1.json",
        "model-1.4.json",
        prerelease=True,
    ),
    _release("model-1.3.0.1.json", "model-1.3.json"),
    _release("model-1.2.0.1.json", "model-1.2.json"),
]


def test_released_rules_are_picked_as_the_validator_picks_them():
    released = watch.model_assets(RELEASES)

    assert watch.best_asset(released, "1.3")["name"] == "model-1.3.0.1.json"
    assert watch.best_asset(released, "1.4")["name"] == "model-1.4.json"
    assert watch.best_asset(released, "1.5") is None


def test_prerelease_rules_count_only_when_asked():
    every = watch.model_assets(RELEASES, prereleases=True)

    assert watch.best_asset(every, "1.3")["name"] == "model-1.3.0.4.json"
    assert watch.best_asset(every, "1.4")["name"] == "model-1.4.0.1.json"


def test_the_newest_release_holding_a_file_wins_and_drafts_never_count():
    releases = [
        _release("model-1.3.0.9.json", draft=True),
        {"assets": [_asset("model-1.3.0.1.json", "sha256:new")]},
        {"assets": [_asset("model-1.3.0.1.json", "sha256:old")]},
    ]

    asset = watch.best_asset(watch.model_assets(releases), "1.3")

    assert asset["digest"] == "sha256:new"


def test_a_version_prefix_matches_whole_components():
    assets = {
        v: _asset(f"model-{v}.json") for v in ("1.3", "1.3.0.2", "1.30", "1.3.0.10")
    }

    assert watch.best_asset(assets, "1.3")["name"] == "model-1.3.0.10.json"


def test_released_13_failing_only_the_tolerated_rule_is_the_expected_answer():
    check = watch.check_released_13(
        _asset("model-1.3.0.1.json", PINNED_DIGEST),
        _report("model-1.3.0.1.json", TOLERATED),
    )

    assert check.alert is None
    assert check.outcome == "fails only the tolerated rule"


def test_released_13_passing_strict_says_the_tolerance_can_go():
    check = watch.check_released_13(
        _asset("model-1.3.0.4.json"), _report("model-1.3.0.4.json")
    )

    assert "drop the tolerated rule" in check.alert
    assert check.outcome.startswith("passes")


def test_a_new_released_13_file_with_the_same_outcome_asks_to_repin():
    check = watch.check_released_13(
        _asset("model-1.3.0.2.json", "sha256:other"),
        _report("model-1.3.0.2.json", TOLERATED),
    )

    assert "pin the gate to the new file" in check.alert


def test_released_13_failing_another_rule_needs_a_look():
    failures = [*TOLERATED, "CAU-BilledCost-C-001-M"]
    check = watch.check_released_13(
        _asset("model-1.3.0.1.json", PINNED_DIGEST),
        _report("model-1.3.0.1.json", failures),
    )

    assert "CAU-BilledCost-C-001-M" in check.alert


def test_released_13_that_does_not_run_is_reported():
    check = watch.check_released_13(_asset("model-1.3.0.1.json", PINNED_DIGEST), None)

    assert "does not run" in check.alert


def test_released_14_crashing_the_validator_is_the_expected_answer():
    check = watch.check_released_14(
        _asset("model-1.4.json"), _report("model-1.4.json", summary=None), branch=True
    )

    assert check.alert is None
    assert "Active-edge cycle" in check.outcome


def test_released_14_passing_says_the_branch_can_be_merged():
    check = watch.check_released_14(
        _asset("model-1.4.0.2.json"), _report("model-1.4.0.2.json"), branch=True
    )

    assert "can be merged" in check.alert


def test_released_14_running_but_failing_asks_to_update_the_branch():
    check = watch.check_released_14(
        _asset("model-1.4.0.2.json"),
        _report("model-1.4.0.2.json", ["CAU-InvoiceDetailId-C-000-C"]),
        branch=True,
    )

    assert "CAU-InvoiceDetailId-C-000-C: update the branch" in check.alert


def test_a_missing_branch_is_reported_once():
    checks = [
        watch.check_released_13(
            _asset("model-1.3.0.1.json", PINNED_DIGEST),
            _report("model-1.3.0.1.json", TOLERATED),
        ),
        watch.check_released_14(_asset("model-1.4.json"), None, branch=False),
        watch.check_branch_gate(None, None, branch=False),
    ]

    assert watch.render(checks, "2.2.1").count("branch is missing") == 1


def test_the_branch_gate_passing_on_the_newest_prerelease_is_the_expected_answer():
    check = watch.check_branch_gate(
        _asset("model-1.4.0.1.json"), _report("model-1.4.0.1.json"), branch=True
    )

    assert check.alert is None


def test_a_newer_prerelease_asks_to_validate_the_branch_on_it():
    check = watch.check_branch_gate(
        _asset("model-1.4.0.2.json"), _report("model-1.4.0.1.json"), branch=True
    )

    assert "`model-1.4.0.2.json`" in check.alert


def test_a_failing_branch_gate_is_reported():
    failing = _report("model-1.4.0.1.json", ["CAU-InvoiceId-C-001-M"])

    check = watch.check_branch_gate(_asset("model-1.4.0.1.json"), failing, branch=True)
    unrun = watch.check_branch_gate(_asset("model-1.4.0.1.json"), None, branch=True)

    assert "CAU-InvoiceId-C-001-M" in check.alert
    assert "did not run" in unrun.alert


def test_the_marker_names_the_set_of_answers():
    a = watch.Check("x", "f", "o", "alert A")
    b = watch.Check("y", "f", "o", "alert B")
    quiet = watch.Check("z", "f", "o")

    assert watch.marker([a, b, quiet]) == watch.marker([b, a, a])
    assert watch.marker([a]) != watch.marker([a, b])


def test_an_answer_already_posted_is_not_posted_again():
    mark = "<!-- focus-watch:abc -->"
    closed = {"state": "CLOSED", "body": f"report\n{mark}", "comments": []}
    commented = {"state": "OPEN", "body": "", "comments": [{"body": mark}]}
    other = {"state": "OPEN", "body": "<!-- focus-watch:def -->", "comments": []}

    assert watch.already_posted([closed], mark)
    assert watch.already_posted([commented], mark)
    assert not watch.already_posted([other], mark)


def test_a_quiet_week_says_there_is_nothing_to_do():
    body = watch.render([watch.Check("x", "f", "o")], "2.2.1")

    assert "nothing to do" in body
    assert "Needs attention" not in body
