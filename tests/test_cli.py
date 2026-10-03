import csv
import json

import pytest

from tokencur import __version__, cli, sources
from tokencur.ingest import claude_code


def _session(path, request_id: str, timestamp: str) -> None:
    """One $5.00 Claude Code call (1M input tokens on claude-opus-4-8)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "type": "assistant",
                "sessionId": path.stem,
                "requestId": request_id,
                "timestamp": timestamp,
                "message": {
                    "id": f"msg_{request_id}",
                    "model": "claude-opus-4-8",
                    "usage": {"input_tokens": 1_000_000, "output_tokens": 0},
                },
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture
def logs(tmp_path, monkeypatch):
    """Calls on the last second of August, the first of September and the
    first of October: the edges a billing period must get right."""
    root = tmp_path / "projects" / "workspace"
    _session(root / "aug.jsonl", "req_aug", "2026-08-31T23:59:59.999Z")
    _session(root / "sep.jsonl", "req_sep", "2026-09-01T00:00:00.000Z")
    _session(root / "oct.jsonl", "req_oct", "2026-10-01T00:00:00.000Z")
    monkeypatch.setattr(
        sources,
        "DEFAULT_SOURCES",
        ((root.parent, claude_code.iter_usage_records),),
    )


def test_version(capsys):
    with pytest.raises(SystemExit) as exit_:
        cli.main(["--version"])

    assert exit_.value.code == 0
    assert capsys.readouterr().out.strip() == f"tokencur {__version__}"


def test_help_lists_every_command(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])

    out = capsys.readouterr().out
    for command in ("report", "export", "recommend", "observatory", "prices"):
        assert command in out


@pytest.mark.usefixtures("logs")
def test_no_command_runs_the_report(capsys):
    assert cli.main([]) == 0

    assert "API-EQUIVALENT TOTAL (showback): $15.00" in capsys.readouterr().out


@pytest.mark.usefixtures("logs")
def test_a_period_includes_since_and_excludes_until(capsys):
    assert cli.main(["report", "--since", "2026-09-01", "--until", "2026-10-01"]) == 0

    out = capsys.readouterr().out
    assert "1 model call 2026-09-01 to 2026-10-01 (UTC, end excluded)" in out
    assert "API-EQUIVALENT TOTAL (showback): $5.00" in out


@pytest.mark.usefixtures("logs")
def test_export_writes_one_billing_period(tmp_path):
    out = tmp_path / "september.csv"

    assert (
        cli.main(["export", str(out), "--since", "2026-09-01", "--until", "2026-10-01"])
        == 0
    )

    with out.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert [r["ChargePeriodStart"] for r in rows] == ["2026-09-01T00:00:00Z"]


@pytest.mark.usefixtures("logs")
def test_an_empty_period_says_so(capsys):
    assert cli.main(["report", "--since", "2030-01-01"]) == 1

    assert "no usage since 2030-01-01" in capsys.readouterr().err


@pytest.mark.parametrize(
    "args",
    [
        ["report", "--since", "last-month"],
        ["report", "--since", "2026-10-01", "--until", "2026-09-01"],
        ["report", "--since", "2026-09-01", "--until", "2026-09-01"],
    ],
)
def test_malformed_or_empty_ranges_are_usage_errors(args, capsys):
    with pytest.raises(SystemExit) as exit_:
        cli.main(args)

    assert exit_.value.code == 2
    assert "error" in capsys.readouterr().err


def test_an_explicit_root_is_read_but_never_stored(tmp_path, capsys):
    root = tmp_path / "elsewhere" / "workspace"
    _session(root / "x.jsonl", "req_x", "2026-09-15T12:00:00.000Z")

    assert cli.main(["report", str(root.parent)]) == 0
    assert "$5.00" in capsys.readouterr().out
    assert cli.main(["report", "--until", "2026-09-15", str(root.parent)]) == 1
    assert "no usage before 2026-09-15" in capsys.readouterr().err
    assert cli.main(["report", str(tmp_path / "missing")]) == 1
    assert "does not exist" in capsys.readouterr().err


def test_export_names_what_reproduces_it(tmp_path, logs, capsys):
    from tokencur.pricing import provenance

    assert cli.main(["export", str(tmp_path / "out.csv")]) == 0
    assert f"({provenance()})" in capsys.readouterr().err


def test_export_reports_what_it_skipped(tmp_path, capsys):
    root = tmp_path / "logs" / "workspace"
    _session(root / "ok.jsonl", "req_ok", "2026-09-15T12:00:00.000Z")
    _session(root / "undated.jsonl", "req_undated", "")
    unpriced = root / "unpriced.jsonl"
    _session(unpriced, "req_unpriced", "2026-09-15T12:00:00.000Z")
    unpriced.write_text(
        unpriced.read_text(encoding="utf-8").replace(
            "claude-opus-4-8", "mystery-model"
        ),
        encoding="utf-8",
    )

    assert cli.main(["export", str(tmp_path / "out.csv"), str(root.parent)]) == 0

    err = capsys.readouterr().err
    assert "wrote 1 FOCUS charge rows" in err
    assert "skipped unpriced usage: mystery-model x1" in err
    assert "skipped undated usage: 1 records" in err


@pytest.mark.usefixtures("logs")
def test_recommend_runs_through_the_cli_and_its_old_entry_point(capsys):
    from tokencur import recommend_cli

    assert cli.main(["recommend"]) == 0
    assert "Total what-if headroom" in capsys.readouterr().out
    assert recommend_cli.main(["recommend_cli", "--since", "2030-01-01"]) == 1
    assert "no usage since 2030-01-01" in capsys.readouterr().err


@pytest.mark.usefixtures("logs")
def test_the_static_pages_render_where_asked(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)  # no subscriptions.json, no git history here

    assert cli.main(["observatory", "site/observatory"]) == 0
    assert cli.main(["prices", "site/prices"]) == 0

    assert (tmp_path / "site/observatory/index.html").exists()
    assert (tmp_path / "site/observatory/data.json").exists()
    assert (tmp_path / "site/prices/index.html").exists()
    out = capsys.readouterr().out
    assert "observatory written to site/observatory" in out.replace("\\", "/")
    assert "prices page written to site/prices (0 events" in out.replace("\\", "/")


def test_python_dash_m_runs_the_same_cli():
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "tokencur", "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == f"tokencur {__version__}"


@pytest.mark.usefixtures("logs")
def test_report_shows_the_contracted_total_under_a_discount(tmp_path, capsys):
    discounts = tmp_path / "discounts.json"
    discounts.write_text(
        json.dumps({"discounts": {"Anthropic": 0.2}}), encoding="utf-8"
    )

    assert cli.main(["report", "--discounts", str(discounts)]) == 0

    out = capsys.readouterr().out
    assert "API-EQUIVALENT TOTAL (showback): $15.00" in out
    assert "CONTRACTED TOTAL (after negotiated discounts): $12.00" in out


@pytest.mark.usefixtures("logs")
def test_report_converts_totals_at_a_rate_you_give(capsys):
    assert cli.main(["report", "--currency", "MXN", "--fx-rate", "18.37"]) == 0

    assert "= MXN 275.55 at 18.37 MXN per USD (rate given)" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ('{"discounts": {"Anthropic": 1.5}}', "between 0 and 1"),
        ('{"discounts": {"Anthropic": "10%"}}', "between 0 and 1"),
        ("not json", "not valid JSON"),
    ],
)
def test_a_bad_discounts_file_is_a_clear_error(tmp_path, capsys, content, message):
    bad = tmp_path / "discounts.json"
    bad.write_text(content, encoding="utf-8")

    assert cli.main(["report", "--discounts", str(bad)]) == 1
    assert message in capsys.readouterr().err


@pytest.mark.parametrize(
    "args",
    [
        ["--currency", "MXN"],
        ["--fx-rate", "18.37"],
        ["--currency", "pesos", "--fx-rate", "18"],
        ["--currency", "MXN", "--fx-rate", "-1"],
    ],
)
def test_currency_needs_a_code_and_a_positive_rate(args):
    with pytest.raises(SystemExit) as exit_:
        cli.main(["report", *args])
    assert exit_.value.code == 2
