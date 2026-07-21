"""Static public page: the price card, and every time it moved.

``python -m tokencur.prices [output_dir]`` renders ``docs/prices/``:

- **The curated card** — the hand-maintained Anthropic rates (dated,
  sourced). This is the layer that wins when valuing usage.
- **Coverage** — how many models the vendored LiteLLM snapshot extends
  the card to, and when it was last fetched.
- **The change log** — a dated timeline built from the git history of
  the snapshot file. The daily price-watch action commits only when
  rates actually move, so each commit is a real price-change event;
  this page is that action's public face.

No runtime dependencies and no external requests: the git walk happens
at generation time, the rendered page is self-contained.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

from tokencur.pricing import AS_OF, RATE_CARD, SOURCE

DEFAULT_OUTPUT = Path("docs") / "prices"
SNAPSHOT_REL = "src/tokencur/pricing_data/litellm_snapshot.json"


@dataclass(frozen=True)
class ModelDelta:
    """One model's rate movement between two snapshot revisions."""

    model: str
    field: str  # "input" | "output"
    old: float  # USD per MTok
    new: float


@dataclass(frozen=True)
class PriceChange:
    """All movements recorded by a single snapshot commit."""

    date: str  # YYYY-MM-DD
    sha: str
    added: list[str]
    removed: list[str]
    changed: list[ModelDelta]
    introduced: bool = False  # the root commit that first created the file

    @property
    def is_introduction(self) -> bool:
        return self.introduced


_PER_TOKEN_TO_MTOK = 1_000_000
_FIELDS = (("input", "input_cost_per_token"), ("output", "output_cost_per_token"))


def diff_models(before: dict, after: dict) -> tuple[list[str], list[str], list[ModelDelta]]:
    """Diff two ``{model: entry}`` price maps.

    Returns (added, removed, changed). A change is any movement in the
    input or output per-token cost, surfaced in USD/MTok. Pure — no git,
    no IO — so it carries the test weight; the git walk around it stays
    thin.
    """
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed: list[ModelDelta] = []
    for model in sorted(set(before) & set(after)):
        b, a = before[model], after[model]
        for field, key in _FIELDS:
            old = b.get(key)
            new = a.get(key)
            if old != new and old is not None and new is not None:
                changed.append(
                    ModelDelta(
                        model=model,
                        field=field,
                        old=old * _PER_TOKEN_TO_MTOK,
                        new=new * _PER_TOKEN_TO_MTOK,
                    )
                )
    return added, removed, changed


def _git(repo_root: Path, *args: str) -> str | None:
    """Run a git command, returning stdout or None on any failure."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return result.stdout


def _models_at(repo_root: Path, revision: str, rel_path: str) -> dict | None:
    """Parse the snapshot's ``models`` map at a git revision, or None."""
    blob = _git(repo_root, "show", f"{revision}:{rel_path}")
    if blob is None:
        return None
    try:
        return json.loads(blob).get("models", {})
    except json.JSONDecodeError:
        return None


def price_changes(
    repo_root: Path = Path("."), rel_path: str = SNAPSHOT_REL
) -> list[PriceChange]:
    """Build the dated change timeline from the snapshot's git history.

    Newest first. Each commit that touched the snapshot is compared to
    its parent; the root/introduction commit (no parent) is reported as
    an introduction rather than hundreds of spurious additions. Returns
    an empty list when git or the history is unavailable, so the page
    still renders from the current card alone.
    """
    log = _git(repo_root, "log", "--format=%H%x09%aI", "--", rel_path)
    if not log:
        return []

    changes: list[PriceChange] = []
    for line in log.strip().splitlines():
        sha, _, iso = line.partition("\t")
        if not sha:
            continue
        after = _models_at(repo_root, sha, rel_path)
        if after is None:
            continue
        parent = _models_at(repo_root, f"{sha}^", rel_path)
        introduced = not parent  # no parent blob = the file's root commit
        added, removed, changed = diff_models(parent or {}, after)
        if not (added or removed or changed):
            continue
        changes.append(
            PriceChange(
                date=iso[:10], sha=sha[:9],
                added=added, removed=removed, changed=changed,
                introduced=introduced,
            )
        )
    return changes


def curated_card() -> list[dict]:
    """The hand-maintained Anthropic rates, as display rows (USD/MTok)."""
    return [
        {
            "model": model,
            "input": rates.input,
            "output": rates.output,
            "cache_read": rates.cache_read,
        }
        for model, rates in RATE_CARD.items()
    ]


def snapshot_meta() -> dict:
    """Coverage stats for the vendored community snapshot."""
    path = resources.files("tokencur").joinpath("pricing_data/litellm_snapshot.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        "models": len(data.get("models", {})),
        "fetched": data.get("_meta", {}).get("fetched", "unknown"),
        "source": data.get("_meta", {}).get("source", ""),
    }


def _usd(x: float) -> str:
    return f"${x:,.2f}"


def _delta_arrow(old: float, new: float) -> str:
    direction = "up" if new > old else "down"
    sign = "▲" if new > old else "▼"
    pct = 100 * (new - old) / old if old else 0.0
    return (
        f'<span class="delta {direction}">{sign} {abs(pct):.0f}%</span>'
        f'<span class="rate">{_usd(old)} → {_usd(new)}</span>'
    )


def _card_table(rows: list[dict]) -> str:
    body = "".join(
        f"<tr><td>{r['model']}</td>"
        f"<td>{_usd(r['input'])}</td>"
        f"<td>{_usd(r['output'])}</td>"
        f"<td>{_usd(r['cache_read'])}</td></tr>"
        for r in rows
    )
    return (
        "<table><thead><tr><th>model</th><th>input</th><th>output</th>"
        "<th>cache read</th></tr></thead><tbody>" + body + "</tbody></table>"
    )


def _timeline(changes: list[PriceChange]) -> str:
    if not changes:
        return (
            '<p class="muted">No rate changes recorded yet. The daily price-watch '
            "commits here the first time a published rate moves.</p>"
        )
    items = []
    for c in changes:
        if c.is_introduction:
            detail = f'<p class="muted">Snapshot introduced — {len(c.added)} models tracked.</p>'
        else:
            parts = []
            for d in c.changed:
                parts.append(
                    f'<div class="row"><span class="model">{d.model}</span>'
                    f'<span class="field">{d.field}</span>{_delta_arrow(d.old, d.new)}</div>'
                )
            if c.added:
                parts.append(
                    f'<div class="row added">+ added: {", ".join(c.added)}</div>'
                )
            if c.removed:
                parts.append(
                    f'<div class="row removed">− removed: {", ".join(c.removed)}</div>'
                )
            detail = "".join(parts)
        items.append(
            f'<li><div class="when"><span class="date">{c.date}</span>'
            f'<span class="sha">{c.sha}</span></div>'
            f'<div class="what">{detail}</div></li>'
        )
    return f'<ul class="timeline">{"".join(items)}</ul>'


def render_html(rows: list[dict], meta: dict, changes: list[PriceChange]) -> str:
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>tokencur — price card</title>
<style>
:root {{
  --surface:#ffffff; --card:#f6f7f9; --ink:#1a2733; --ink-2:#51606e; --muted:#7a8794;
  --grid:#e3e7eb; --up:#b23b3b; --down:#1baf7a; --accent:#2a78d6;
}}
@media (prefers-color-scheme: dark) {{ :root {{
  --surface:#101418; --card:#191f26; --ink:#e8edf2; --ink-2:#aab6c2; --muted:#7d8996;
  --grid:#2a323b; --up:#e06666; --down:#199e70; --accent:#3987e5;
}} }}
* {{ box-sizing:border-box; margin:0 }}
body {{ background:var(--surface); color:var(--ink); font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; padding:32px 20px 48px; max-width:840px; margin:0 auto }}
h1 {{ font-size:1.5rem }} h2 {{ font-size:1.05rem; margin:34px 0 12px }}
.sub {{ color:var(--ink-2); margin:4px 0 22px }}
.muted {{ color:var(--muted); font-size:.85rem }}
.meta {{ display:flex; gap:22px; flex-wrap:wrap; background:var(--card); border-radius:10px; padding:14px 18px; margin-bottom:8px }}
.meta div span {{ display:block }} .meta .n {{ font-size:1.2rem; font-weight:650; font-variant-numeric:tabular-nums }}
.meta .l {{ color:var(--ink-2); font-size:.78rem }}
table {{ border-collapse:collapse; width:100%; font-size:.86rem; margin-top:6px }}
th,td {{ text-align:left; padding:6px 12px 6px 0; border-bottom:1px solid var(--grid); font-variant-numeric:tabular-nums }}
th {{ color:var(--muted); font-weight:500 }}
td:first-child {{ font-family:ui-monospace,"SF Mono",Menlo,monospace; font-size:.82rem }}
.timeline {{ list-style:none; padding:0; display:flex; flex-direction:column; gap:14px }}
.timeline li {{ display:grid; grid-template-columns:130px 1fr; gap:14px; padding-bottom:14px; border-bottom:1px solid var(--grid) }}
.when {{ display:flex; flex-direction:column }}
.date {{ font-weight:650; font-variant-numeric:tabular-nums }}
.sha {{ color:var(--muted); font-family:ui-monospace,monospace; font-size:.76rem }}
.row {{ display:flex; align-items:baseline; gap:10px; flex-wrap:wrap; padding:2px 0 }}
.model {{ font-family:ui-monospace,monospace; font-size:.82rem }}
.field {{ color:var(--muted); font-size:.76rem; text-transform:uppercase; letter-spacing:.04em }}
.delta {{ font-weight:650; font-size:.82rem; font-variant-numeric:tabular-nums }}
.delta.up {{ color:var(--up) }} .delta.down {{ color:var(--down) }}
.rate {{ color:var(--ink-2); font-size:.8rem; font-variant-numeric:tabular-nums }}
.row.added {{ color:var(--down); font-size:.82rem }} .row.removed {{ color:var(--up); font-size:.82rem }}
footer {{ margin-top:38px; color:var(--muted); font-size:.8rem }}
footer a {{ color:var(--ink-2) }}
</style></head><body>
<h1>tokencur — the price card</h1>
<p class="sub">The public API list rates tokencur values usage against, and every time
they moved. Prices in USD per million tokens (MTok).</p>

<h2>Curated rates — Anthropic (the layer that wins)</h2>
<p class="muted">Hand-maintained and sourced, as of {AS_OF}. Cache reads are 0.1× input;
writes 1.25× (5-min) / 2× (1-hour).</p>
{_card_table(rows)}

<h2>Extended coverage</h2>
<div class="meta">
  <div><span class="n">{meta["models"]}</span><span class="l">models via community snapshot</span></div>
  <div><span class="n">{meta["fetched"]}</span><span class="l">snapshot last fetched</span></div>
</div>
<p class="muted">The curated card above wins; the vendored
<a href="{meta["source"]}">LiteLLM</a> snapshot extends coverage to OpenAI, Gemini,
DeepSeek, Kimi/Moonshot, GLM and Ollama for fallback valuation.</p>

<h2>Change log — straight from the git history</h2>
<p class="muted">A daily action refreshes the snapshot and commits only when a published
rate actually moves, so each entry below is a real price change.</p>
{_timeline(changes)}

<footer>Curated source: <a href="{SOURCE}">Anthropic pricing</a> · generated {generated}
· <a href="https://github.com/pach-boop/tokencur">tokencur</a></footer>
</body></html>
"""


def write_site(outdir: Path, changes: list[PriceChange]) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "index.html").write_text(
        render_html(curated_card(), snapshot_meta(), changes), encoding="utf-8"
    )


def main(argv: list[str]) -> int:
    outdir = Path(argv[1]) if len(argv) > 1 else DEFAULT_OUTPUT
    changes = price_changes()
    write_site(outdir, changes)
    moved = sum(len(c.changed) for c in changes)
    gained = sum(len(c.added) for c in changes if not c.introduced)
    print(
        f"prices page written to {outdir} "
        f"({len(changes)} events: {moved} rate moves, {gained} models added)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
