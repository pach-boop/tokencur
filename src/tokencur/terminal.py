"""Formatting shared by the commands that print to a terminal."""

from __future__ import annotations

from pathlib import Path


def table(header: tuple[str, ...], rows: list[tuple[str, ...]]) -> list[str]:
    """Columns as wide as their widest cell: first column left, the rest right.

    Fixed widths broke once real totals passed a billion tokens or a
    model id ran long, and adjacent columns ran together.
    """
    widths = [
        max(len(cell) for cell in column) for column in zip(header, *rows, strict=True)
    ]

    def line(cells: tuple[str, ...]) -> str:
        first = cells[0].ljust(widths[0])
        rest = (
            cell.rjust(width) for cell, width in zip(cells[1:], widths[1:], strict=True)
        )
        return "  ".join((first, *rest)).rstrip()

    return [line(header), *(line(row) for row in rows)]


def home_relative(path: Path) -> str:
    """``path`` with the home directory written as ``~``."""
    try:
        return str(Path("~") / path.relative_to(Path.home()))
    except ValueError:
        return str(path)
