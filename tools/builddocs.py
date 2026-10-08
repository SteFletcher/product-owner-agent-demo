"""Build docs/*.md into a static site with Mermaid rendered client-side.

    python tools/builddocs.py [--out site]

Used by .github/workflows/pages.yml. Needs the `markdown` package only.
"""
from __future__ import annotations

import argparse
import html
import re
import shutil
import sys
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
ORDER = ["index", "executive-summary", "architecture", "graph", "engine-selection", "configuration", "benchmark", "benchmark-results"]

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root{{color-scheme:light dark;--fg:#1b1f24;--bg:#fff;--muted:#57606a;--line:#d0d7de;--accent:#0969da;--code:#f6f8fa}}
@media(prefers-color-scheme:dark){{:root{{--fg:#e6edf3;--bg:#0d1117;--muted:#8b949e;--line:#30363d;--accent:#58a6ff;--code:#161b22}}}}
body{{font-family:system-ui,-apple-system,sans-serif;color:var(--fg);background:var(--bg);margin:0;line-height:1.55}}
nav{{border-bottom:1px solid var(--line);padding:.6rem 1rem;display:flex;gap:1rem;flex-wrap:wrap;font-size:.95rem}}
nav a{{color:var(--accent);text-decoration:none}} nav a.current{{font-weight:600;text-decoration:underline}}
main{{max-width:62rem;margin:0 auto;padding:1rem 1rem 4rem}}
table{{border-collapse:collapse;font-size:.9rem;display:block;overflow-x:auto;max-width:100%}}
th,td{{border:1px solid var(--line);padding:.35rem .55rem;vertical-align:top}} th{{background:var(--code)}}
pre{{background:var(--code);padding:.8rem;overflow-x:auto;border-radius:6px}} code{{font-size:.9em}}
.mermaid{{background:transparent;text-align:center;margin:1rem 0}}
h1,h2,h3{{line-height:1.25}} blockquote{{border-left:4px solid var(--line);margin:0;padding:0 1rem;color:var(--muted)}}
</style></head><body>
<nav>{nav}</nav>
<main>{body}</main>
<script type="module">
import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
const dark = matchMedia("(prefers-color-scheme: dark)").matches;
mermaid.initialize({{startOnLoad:true, theme: dark ? "dark" : "default"}});
</script>
</body></html>
"""


def render(md: str) -> str:
    # Mermaid fences become <pre class="mermaid"> before the markdown pass so they are not escaped twice.
    blocks: list[str] = []

    def stash(m: re.Match) -> str:
        blocks.append(m.group(1))
        return f"\n\n@@MERMAID{len(blocks) - 1}@@\n\n"
    md = re.sub(r"```mermaid\n(.*?)```", stash, md, flags=re.DOTALL)
    body = markdown.markdown(md, extensions=["tables", "fenced_code", "toc", "sane_lists"])
    for i, src in enumerate(blocks):
        body = body.replace(f"<p>@@MERMAID{i}@@</p>", f'<pre class="mermaid">{html.escape(src)}</pre>')
    # relative .md links -> .html
    return re.sub(r'href="([^":#]+)\.md(#[^"]*)?"', r'href="\1.html\2"', body)


def title_of(md: str, fallback: str) -> str:
    m = re.search(r"^# (.+)$", md, flags=re.MULTILINE)
    return m.group(1).strip() if m else fallback


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="site")
    args = ap.parse_args(argv)
    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    pages = {p.stem: p for p in DOCS.glob("*.md")}
    names = [n for n in ORDER if n in pages] + sorted(n for n in pages if n not in ORDER)
    titles = {n: title_of(pages[n].read_text(), n.replace("-", " ").title()) for n in names}
    for n in names:
        nav = " · ".join(f'<a href="{m}.html" class="{"current" if m == n else ""}">{html.escape(titles[m])}</a>'
                         for m in names)
        (out / f"{n}.html").write_text(PAGE.format(title=html.escape(titles[n]), nav=nav, body=render(pages[n].read_text())))
    for pattern in ("*.png", "*.jpg", "*.svg"):
        for extra in DOCS.glob(pattern):
            shutil.copy(extra, out / extra.name)
    (out / ".nojekyll").write_text("")
    print(f"built {len(names)} pages into {out}/: {', '.join(names)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
