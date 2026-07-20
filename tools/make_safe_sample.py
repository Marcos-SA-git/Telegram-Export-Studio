#!/usr/bin/env python3
"""Make a shareable, content-free copy of a Telegram HTML export.

Usage:
    python tools/make_safe_sample.py <export_dir> [-o OUTPUT_DIR]
                                     [--scrub-filenames] [--report-only]

The point of this script is to let you hand an export to someone else (a
bug report, a maintainer, an AI assistant) *without* handing over a single
word you or anyone else wrote.

What it removes:
  * every message body — replaced by the literal word "mensaje",
  * every media file — recreated as a 0-byte file with the same name and
    extension, so the folder shape and the HTML references still line up,
  * every real name — replaced by stable pseudonyms ("Persona 1", ...),
    including names appearing in reactions, replies and mentions,
  * every outbound URL — replaced by https://example.invalid/enlace.

What it deliberately KEEPS, because the structure is the thing being
debugged:
  * message ids, dates and the order of everything,
  * the distinction between a real sender header and a forwarded-from
    header (this is what the sender-counting logic trips over),
  * media filenames and the folder layout (unless --scrub-filenames),
  * Telegram's own css/js/images assets, copied verbatim.

It also prints a sender report: how many distinct names appear as real
senders versus how many only ever appear as "forwarded from". A private
chat that reports 100+ participants is the signature of forwarded
authors being counted as chat members.
"""

import argparse
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

# Telegram's own asset folders. They contain no personal data and the
# fusion code treats them specially (dedup by name, never renamed), so
# they are copied byte-for-byte rather than emptied.
ASSET_DIRS = {"css", "js", "images"}

PLACEHOLDER = "mensaje"

# A from_name div wrapped in "forwarded body" is the *original author of a
# forwarded message*, not a participant of this chat. Everything else is a
# real sender header.
FORWARDED_FROM_RE = re.compile(
    r'<div class="forwarded body">\s*\n\s*<div class="from_name">\s*\n'
    r'(.*?)\n', re.DOTALL)
FROM_NAME_RE = re.compile(r'<div class="from_name">\s*\n(.*?)\n', re.DOTALL)

NAME_LINE_RE = re.compile(r'(<div class="from_name">\s*\n)(.*?)(\n)', re.DOTALL)
TITLE_RE = re.compile(r'(<div class="text bold">\s*\n)(.*?)(\n)', re.DOTALL)
TEXT_BODY_RE = re.compile(
    r'(<div class="text">\s*\n)(.*?)(\n\s*</div>)', re.DOTALL)
INITIALS_RE = re.compile(r'(<div class="initials"[^>]*>\s*\n)(.*?)(\n)',
                         re.DOTALL)
ABS_URL_RE = re.compile(r'(href|src)="(https?://[^"]*)"')
MEDIA_REF_RE = re.compile(r'(?:\bsrc|\bhref|\bposter)="([^":#]+)"')


def collect_names(pages):
    """Return (all_names, forwarded_only_names) across every page."""
    real = Counter()
    forwarded = Counter()
    for html in pages:
        fwd = [m.strip() for m in FORWARDED_FROM_RE.findall(html)]
        forwarded.update(fwd)
        every = [m.strip() for m in FROM_NAME_RE.findall(html)]
        # FROM_NAME_RE also matches the forwarded ones; subtract them.
        leftover = Counter(every) - Counter(fwd)
        real.update(leftover)
    return real, forwarded


def build_name_map(real, forwarded):
    """Stable pseudonyms: real senders first, then forwarded-only authors."""
    mapping = {}
    for i, name in enumerate(n for n, _ in real.most_common()):
        mapping[name] = f"Persona {i + 1}"
    n_real = len(mapping)
    extra = [n for n, _ in forwarded.most_common() if n not in mapping]
    for i, name in enumerate(extra):
        mapping[name] = f"Reenviado {i + 1}"
    return mapping, n_real, len(extra)


def scrub_html(html, name_map, file_map):
    html = NAME_LINE_RE.sub(
        lambda m: m.group(1) + name_map.get(m.group(2).strip(),
                                            m.group(2)) + m.group(3), html)
    html = TITLE_RE.sub(
        lambda m: m.group(1) + name_map.get(m.group(2).strip(),
                                            "Chat") + m.group(3), html)
    html = TEXT_BODY_RE.sub(lambda m: m.group(1) + PLACEHOLDER + m.group(3),
                            html)
    html = INITIALS_RE.sub(lambda m: m.group(1) + "P" + m.group(3), html)
    html = ABS_URL_RE.sub(r'\1="https://example.invalid/enlace"', html)

    # Names also show up inside title="..." on reactions, in reply
    # previews and in @mentions. A global pass over the exact strings
    # catches those without needing a rule per context. Longest first so
    # "Ana Maria" is not half-replaced by a rule for "Ana".
    for name in sorted(name_map, key=len, reverse=True):
        if name:
            html = html.replace(name, name_map[name])

    for src, dst in file_map.items():
        html = html.replace(f'"{src}"', f'"{dst}"')
    return html


def main():
    ap = argparse.ArgumentParser(
        description="Create a content-free copy of a Telegram HTML export.")
    ap.add_argument("export_dir", type=Path)
    ap.add_argument("-o", "--output", type=Path,
                    help="destination folder (default: <export_dir>_safe)")
    ap.add_argument("--scrub-filenames", action="store_true",
                    help="also rename media files to file_0001.ext, in case "
                         "the original filenames are themselves revealing")
    ap.add_argument("--report-only", action="store_true",
                    help="print the sender report and exit, writing nothing")
    args = ap.parse_args()

    src = args.export_dir
    if not src.is_dir():
        sys.exit(f"No es una carpeta: {src}")
    page_paths = sorted(
        src.glob("messages*.html"),
        key=lambda p: int(re.search(r"(\d*)\.html$", p.name).group(1) or 0))
    if not page_paths:
        sys.exit(f"{src} no contiene messages.html — no parece un export "
                 f"HTML de Telegram")

    pages = [p.read_text(encoding="utf-8", errors="replace")
             for p in page_paths]
    real, forwarded = collect_names(pages)
    name_map, n_real, n_fwd = build_name_map(real, forwarded)

    print(f"Páginas:                       {len(page_paths)}")
    print(f"Remitentes reales:             {n_real}")
    print(f"Autores solo de reenvíos:      {n_fwd}")
    print(f"Total de nombres 'from_name':  {n_real + n_fwd}")
    if n_real <= 2 < n_real + n_fwd:
        print("\n  -> Chat privado contado como grupo: la cuenta de "
              f"participantes\n     daría {n_real + n_fwd} en vez de "
              f"{n_real} porque incluye los reenvíos.")
    print()
    if args.report_only:
        return

    out = args.output or src.parent / f"{src.name}_safe"
    if out.exists():
        sys.exit(f"El destino ya existe, bórralo o elige otro: {out}")
    out.mkdir(parents=True)

    # Pass 1: recreate the tree. Assets are copied intact; every other
    # non-HTML file becomes an empty file with the same name, which keeps
    # the HTML references resolvable and the folder shape faithful.
    file_map = {}
    counter = 0
    for path in sorted(src.rglob("*")):
        rel = path.relative_to(src)
        top = rel.parts[0] if rel.parts else ""
        if path.is_dir():
            (out / rel).mkdir(parents=True, exist_ok=True)
            continue
        if top in ASSET_DIRS:
            (out / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, out / rel)
            continue
        if path.suffix.lower() == ".html":
            continue
        dst_rel = rel
        if args.scrub_filenames:
            counter += 1
            dst_rel = rel.with_name(f"file_{counter:04d}{path.suffix}")
            file_map[rel.as_posix()] = dst_rel.as_posix()
        (out / dst_rel).parent.mkdir(parents=True, exist_ok=True)
        (out / dst_rel).touch()

    # Pass 2: rewrite the HTML pages.
    for path, html in zip(page_paths, pages):
        (out / path.name).write_text(scrub_html(html, name_map, file_map),
                                     encoding="utf-8")
    for path in src.rglob("*.html"):
        if path.parent == src and path.name.startswith("messages"):
            continue
        rel = path.relative_to(src)
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        (out / rel).write_text(
            scrub_html(path.read_text(encoding="utf-8", errors="replace"),
                       name_map, file_map), encoding="utf-8")

    n_media = sum(1 for p in out.rglob("*")
                  if p.is_file() and p.suffix.lower() != ".html"
                  and (p.relative_to(out).parts[0] not in ASSET_DIRS))
    print(f"Escrito en: {out}")
    print(f"  {len(page_paths)} páginas HTML con los textos sustituidos")
    print(f"  {n_media} archivos de media vaciados (0 bytes)")
    print(f"  {len(name_map)} nombres seudonimizados")
    print("\nRevisa el resultado antes de compartirlo.")


if __name__ == "__main__":
    main()
