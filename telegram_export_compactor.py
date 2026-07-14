#!/usr/bin/env python3
"""Telegram Export Compactor — repaginate a Telegram HTML export in place.

Reduces (or increases) the number of messagesN.html pages of an existing
export — either a real Telegram export or one produced by
telegram_export_fuser.py. Media folders are left untouched; only the
messages*.html pages are rewritten, with reply links fixed to point at
the right page.

Usage:
    python telegram_export_compactor.py <export_dir> --files 1
    python telegram_export_compactor.py <export_dir> --size 5MB

--files N   repaginate into at most N pages (default: 1, a single file)
--size S    repaginate into pages of roughly S bytes each (e.g. 5MB)

Must live next to telegram_export_fuser.py (it reuses its parser/writer).
"""

import argparse
import math
import sys
from pathlib import Path

from telegram_export_fuser import (
    FOOTER, merge_messages, parse_export, parse_size, partition_pages,
    report_stage, write_pages,
)


def compact(export_dir: Path, max_files: int | None, page_bytes: float | None):
    report_stage("scan", name=export_dir.name)
    messages, header_html, sender_headers = parse_export(export_dir)
    old_pages = sorted(p.name for p in export_dir.glob("messages*.html"))

    # single-export "merge": sorts by id and dedups anchored service blocks
    merged = merge_messages([(messages, header_html, sender_headers)])
    real = sum(1 for m in merged if m.msg_id > 0)
    print(f"Parsed {real} messages from {len(old_pages)} page(s) "
          f"in {export_dir}")

    overhead = len(header_html.encode("utf-8")) + len(FOOTER.encode("utf-8"))
    if page_bytes is None:
        # target a page count: derive a size limit and grow it until the
        # partition fits in max_files pages
        total = sum(len(m.html.encode("utf-8")) + 200 for m in merged)
        page_bytes = max(math.ceil(total / max_files) + overhead, 32 * 1024)
        while len(partition_pages(merged, page_bytes - overhead)) > max_files:
            page_bytes = math.ceil(page_bytes * 1.1)

    written = write_pages(merged, header_html, export_dir, page_bytes,
                          sender_headers)
    removed = sorted(set(old_pages) - set(written))
    print(f"Rewrote {len(written)} page(s): {', '.join(written)}")
    if removed:
        print(f"Removed {len(removed)} old page(s): {', '.join(removed)}")
    return {
        "messages": real,
        "pages": written,
        "removed": removed,
        "out_dir": str(export_dir),
    }


def main():
    ap = argparse.ArgumentParser(
        description="Repaginate a Telegram HTML export in place, reducing "
                    "the number of messagesN.html files.")
    ap.add_argument("export", help="export folder containing messages*.html")
    group = ap.add_mutually_exclusive_group()
    group.add_argument("-f", "--files", type=int,
                       help="maximum number of pages (default: 1)")
    group.add_argument("-s", "--size",
                       help="approximate size per page, e.g. 5MB")
    args = ap.parse_args()

    export_dir = Path(args.export).resolve()
    if not export_dir.is_dir():
        sys.exit(f"error: {export_dir} is not a directory")

    if args.size is not None:
        page_bytes = parse_size(args.size)
        max_files = None
    else:
        max_files = args.files if args.files is not None else 1
        if max_files < 1:
            sys.exit("error: --files must be >= 1")
        page_bytes = None

    compact(export_dir, max_files, page_bytes)


if __name__ == "__main__":
    main()
