"""Formatting shared by the commands that print to a terminal."""

from __future__ import annotations

from pathlib import Path


def table(
    header: tuple[str, ...], rows: list[tuple[str, ...]], left: int = 1
) -> list[str]:
    """Columns as wide as their widest cell: the first ``left`` columns
    (labels) aligned left, the rest (numbers) right.

    Fixed widths broke once real totals passed a billion tokens or a
    model id ran long, and adjacent columns ran together.
    """
    widths = [
        max(len(cell) for cell in column) for column in zip(header, *rows, strict=True)
    ]

    def line(cells: tuple[str, ...]) -> str:
        return "  ".join(
            cell.ljust(width) if i < left else cell.rjust(width)
            for i, (cell, width) in enumerate(zip(cells, widths, strict=True))
        ).rstrip()

    return [line(header), *(line(row) for row in rows)]


def home_relative(path: Path) -> str:
    """``path`` with the home directory written as ``~``."""
    try:
        return str(Path("~") / path.relative_to(Path.home()))
    except ValueError:
        return str(path)
