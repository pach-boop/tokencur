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
