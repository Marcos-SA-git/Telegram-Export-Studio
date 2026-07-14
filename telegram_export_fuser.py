#!/usr/bin/env python3
"""Telegram Export Fuser — merge multiple Telegram HTML chat exports into one.

Usage:
    python telegram_export_fuser.py <export_dir> <export_dir> [...]
                                    [-o OUTPUT_DIR] [--page-size SIZE]

Each <export_dir> is a folder produced by Telegram Desktop's "Export chat
history" (HTML format), i.e. it contains messages.html (and possibly
messages2.html, ...) plus media folders (photos/, video_files/, ...).

The script:
  * parses every messages*.html in every export,
  * deduplicates messages by their Telegram message id,
  * re-sorts everything chronologically (by id) and regenerates the
    per-day date separators,
  * repairs "joined" messages (grouped bubbles without a sender header)
    whose original predecessor is no longer directly above them,
  * copies all referenced media into the output folder (renaming on
    filename collisions with different content, and rewriting the HTML
    references accordingly),
  * writes the merged history paginated Telegram-style (messages.html,
    messages2.html, ...) at roughly --page-size bytes per page
    (default 500KB, like Telegram; 0 = single file), fixing reply links
    that point across pages.
"""

import argparse
import hashlib
import math
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]

DATE_DIVIDER_RE = re.compile(
    r"^\s*\d{1,2}\s+(" + "|".join(MONTHS) + r")\s+\d{4}\s*$")

MSG_OPEN_RE = re.compile(r'<div class="message[^"]*" id="message(-?\d+)">')
DATE_TITLE_RE = re.compile(
    r'<div class="pull_right date details" title="'
    r'(\d{2})\.(\d{2})\.(\d{4}) (\d{2}):(\d{2}):(\d{2})[^"]*"')
FROM_NAME_RE = re.compile(r'<div class="from_name">\s*\n(.*?)\n\s*</div>',
                          re.DOTALL)
USERPIC_RE = re.compile(
    r'(<div class="pull_left userpic_wrap">.*?\n      </div>\n)', re.DOTALL)
MEDIA_REF_RE = re.compile(r'(?:\bsrc|\bhref|\bposter)="([^":#]+)"')
GO_TO_LINK_RE = re.compile(
    r'<a( class="[^"]*")? href="(?:messages\d*\.html)?'
    r'#go_to_message(\d+)"[^>]*>')

ASSET_DIRS = {"css", "js", "images"}
FOOTER = "\n    </div>\n\n   </div>\n\n  </div>\n\n </body>\n\n</html>"
DEFAULT_PAGE_SIZE = "500KB"

# Optional GUI progress hook: callable(kind, payload). kind is "stage"
# (payload: {"key": ..., "frac": 0..1|None, ...extras}) or "warn"
# (payload: str). Unset (None) for CLI use — output stays print-based.
progress_hook = None

# Web (browser) mode: when set, fuse() does NOT copy media itself — the
# host copies files by streaming and provides the collision-rename map:
# {str(export_dir): {old_ref: new_ref}}. HTML references get rewritten
# accordingly. None (default) = normal on-disk copy via copy_media().
media_maps = None


def report_stage(key, frac=None, **extra):
    if progress_hook:
        progress_hook("stage", {"key": key, "frac": frac, **extra})


def report_warn(text):
    if progress_hook:
        progress_hook("warn", text)


@dataclass
class Message:
    msg_id: int                 # positive = real Telegram id
    kind: str                   # "default" | "service"
    joined: bool
    date: datetime | None       # None for service messages without a date
    sender: str | None          # propagated onto joined messages
    html: str                   # the full <div class="message ..."> block
    source: Path                # export dir this block came from
    sort_key: tuple = field(default=(), compare=False)


def parse_size(text: str) -> float:
    """'500KB' / '2MB' / '800000' -> bytes; '0' means unlimited."""
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([kmg]?b?)", text.strip().lower())
    if not m:
        sys.exit(f"error: cannot parse size {text!r} (try 500KB, 2MB, ...)")
    value = float(m.group(1))
    unit = m.group(2).rstrip("b")
    value *= {"": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}[unit]
    return math.inf if value == 0 else value


def extract_message_blocks(html: str):
    """Yield the full block of each top-level message div."""
    div_re = re.compile(r"<div\b|</div>")
    for m in MSG_OPEN_RE.finditer(html):
        depth = 0
        for d in div_re.finditer(html, m.start()):
            depth += 1 if d.group() != "</div>" else -1
            if depth == 0:
                yield html[m.start():html.find(">", d.start()) + 1]
                break


def parse_export(export_dir: Path):
    """Parse all messages*.html in one export dir.

    Returns (messages, header_html, sender_headers) where sender_headers
    maps sender -> (userpic_html, from_name_html) taken from a full
    (non-joined) message of that sender.
    """
    pages = sorted(export_dir.glob("messages*.html"),
                   key=lambda p: int(re.search(r"(\d*)\.html$", p.name)
                                     .group(1) or 0))
    if not pages:
        sys.exit(f"error: no messages*.html found in {export_dir}")

    messages = []
    header_html = None
    sender_headers = {}
    for page in pages:
        html = page.read_text(encoding="utf-8")
        if header_html is None:
            cut = html.find('<div class="history">')
            header_html = html[:cut + len('<div class="history">')]

        current_sender = None
        for block in extract_message_blocks(html):
            msg_id = int(MSG_OPEN_RE.search(block).group(1))
            is_service = 'class="message service"' in block
            joined = "clearfix joined" in block

            date = None
            dm = DATE_TITLE_RE.search(block)
            if dm:
                dd, mo, yy, hh, mi, ss = (int(g) for g in dm.groups())
                date = datetime(yy, mo, dd, hh, mi, ss)

            sender = None
            if is_service:
                body = re.search(
                    r'<div class="body details">\s*\n(.*?)\n\s*</div>',
                    block, re.DOTALL)
                if body and DATE_DIVIDER_RE.match(body.group(1)):
                    continue  # date divider — regenerated after the merge
            else:
                fm = FROM_NAME_RE.search(block)
                if fm:
                    sender = fm.group(1).strip()
                    current_sender = sender
                    um = USERPIC_RE.search(block)
                    if um and sender not in sender_headers:
                        sender_headers[sender] = (um.group(1), fm.group(0))
                elif joined:
                    sender = current_sender

            messages.append(Message(
                msg_id=msg_id,
                kind="service" if is_service else "default",
                joined=joined, date=date, sender=sender,
                html=block, source=export_dir))

    # Sort key: real messages sort by their id; a service message with a
    # negative id stays anchored right after the message it followed.
    anchor, seq = 0, 0
    for msg in messages:
        if msg.msg_id > 0:
            anchor, seq = msg.msg_id, 0
        else:
            seq += 1
        msg.sort_key = (anchor, seq)
    return messages, header_html, sender_headers


def merge_messages(per_export):
    """Deduplicate by message id. Later exports override earlier ones
    (they may contain edited text), except that a full version is never
    replaced by a headerless 'joined' version of the same message."""
    by_id = {}
    order_extra = []  # negative-id service messages (pins etc.)
    for messages, _, _ in per_export:
        for msg in messages:
            if msg.msg_id > 0:
                prev = by_id.get(msg.msg_id)
                if prev is None or not (msg.joined and not prev.joined):
                    by_id[msg.msg_id] = msg
            else:
                order_extra.append(msg)

    merged = list(by_id.values())
    # drop anchored service messages duplicated across exports
    seen = set()
    for msg in order_extra:
        k = (msg.sort_key[0], re.sub(r"\s+", " ", msg.html))
        if k not in seen:
            seen.add(k)
            merged.append(msg)
    merged.sort(key=lambda m: m.sort_key)
    return merged


def unjoin(msg: Message, sender_headers) -> str:
    """Convert a 'joined' block into a full block with sender header."""
    header = sender_headers.get(msg.sender)
    if not header or "clearfix joined" not in msg.html:
        return msg.html
    userpic_html, from_name_html = header
    html = msg.html.replace("clearfix joined", "clearfix", 1)
    html = re.sub(r'(id="message-?\d+">\n)',
                  r"\1\n      " + userpic_html.strip() + "\n",
                  html, count=1)
    html = re.sub(
        r'(<div class="pull_right date details"[^>]*>\s*\n[^\n]*\n\s*</div>)',
        r"\1\n\n       " + from_name_html.strip(),
        html, count=1)
    return html


def date_divider(date: datetime, n: int) -> str:
    label = f"{date.day} {MONTHS[date.month - 1]} {date.year}"
    return (f'     <div class="message service" id="message-{n}">\n\n'
            f'      <div class="body details">\n{label}\n      </div>\n\n'
            f'     </div>')


def sha1(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def copy_media(msg: Message, out_dir: Path, copied: dict) -> str:
    """Copy every relative media file referenced by this message into
    out_dir. Returns the message html with references rewritten when a
    collision forced a rename."""
    html = msg.html
    for ref in set(MEDIA_REF_RE.findall(html)):
        if ref.startswith(("http://", "https://", "tg://", "mailto:")):
            continue
        top = ref.split("/", 1)[0]
        if top in ASSET_DIRS or "/" not in ref:
            continue
        src = msg.source / ref
        if not src.is_file():
            print(f"  warning: missing media {src}", file=sys.stderr)
            report_warn(f"Media no encontrado: {src}")
            continue

        key = (ref, sha1(src))
        if key in copied:
            final_ref = copied[key]
        else:
            final_ref = ref
            dest = out_dir / ref
            n = 2
            while dest.exists() and sha1(dest) != key[1]:
                stem, dot, ext = dest.name.partition(".")
                final_ref = f"{ref.rsplit('/', 1)[0]}/{stem}__{n}{dot}{ext}"
                dest = out_dir / final_ref
                n += 1
            if not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
            copied[key] = final_ref
        if final_ref != ref:
            report_warn(f"Colisión de nombre resuelta: {ref} → {final_ref}")
            html = html.replace(f'"{ref}"', f'"{final_ref}"')
    return html


def apply_media_map(msg: Message) -> str:
    """Web mode: rewrite media references per the host-provided map."""
    mapping = (media_maps or {}).get(str(msg.source), {})
    html = msg.html
    for old, new in mapping.items():
        html = html.replace(f'"{old}"', f'"{new}"')
    return html


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

def page_name(index: int) -> str:
    return "messages.html" if index == 0 else f"messages{index + 1}.html"


def partition_pages(messages, page_bytes: float):
    """Split the message list into pages of roughly page_bytes each.
    Pages are only cut right before a real message, so anchored service
    messages stay with the message they follow."""
    pages, cur, cur_size = [], [], 0
    last_day = None
    for msg in messages:
        size = len(msg.html.encode("utf-8")) + 2
        if msg.msg_id > 0 and msg.date is not None \
                and msg.date.date() != last_day:
            size += 200  # a date divider will be inserted here
            last_day = msg.date.date()
        if cur and msg.msg_id > 0 and cur_size + size > page_bytes:
            pages.append(cur)
            cur, cur_size = [], 0
        cur.append(msg)
        cur_size += size
    if cur:
        pages.append(cur)
    return pages


def render_page(page_msgs, sender_headers):
    """Render one page's blocks: regenerate date dividers (every page
    starts with the current day, like Telegram) and give the first
    message of the page a full sender header."""
    parts = []
    last_day = None
    divider_n = 0
    first_real = True
    for msg in page_msgs:
        html = msg.html
        if msg.msg_id > 0:
            if msg.date is not None and msg.date.date() != last_day:
                divider_n += 1
                parts.append(date_divider(msg.date, divider_n))
                last_day = msg.date.date()
            if msg.joined and first_real:
                html = unjoin(msg, sender_headers)
            first_real = False
        parts.append(html)
    return parts


def write_pages(messages, header_html, out_dir: Path, page_bytes: float,
                sender_headers):
    """Partition, render, fix cross-page reply links and write all
    messages*.html files into out_dir. Removes stale messagesN.html
    pages left over from a previous, longer pagination."""
    report_stage("write")
    overhead = len(header_html.encode("utf-8")) + len(FOOTER.encode("utf-8"))
    effective = max(page_bytes - overhead, 16 * 1024)
    pages = partition_pages(messages, effective)

    id_to_page = {}
    for idx, page in enumerate(pages):
        for msg in page:
            if msg.msg_id > 0:
                id_to_page[msg.msg_id] = idx

    def link_fixer(page_idx):
        def repl(m):
            cls = m.group(1) or ""
            target = int(m.group(2))
            target_page = id_to_page.get(target)
            if target_page is None or target_page == page_idx:
                return (f'<a{cls} href="#go_to_message{target}" '
                        f'onclick="return GoToMessage({target})">')
            return (f'<a{cls} href="{page_name(target_page)}'
                    f'#go_to_message{target}">')
        return repl

    written = []
    for idx, page in enumerate(pages):
        parts = [header_html]
        parts.extend(render_page(page, sender_headers))
        if idx + 1 < len(pages):
            parts.append(f'     <a class="pagination block_link" '
                         f'href="{page_name(idx + 1)}">Next messages</a>')
        body = "\n\n".join(parts) + FOOTER
        body = GO_TO_LINK_RE.sub(link_fixer(idx), body)
        name = page_name(idx)
        (out_dir / name).write_text(body, encoding="utf-8")
        written.append(name)

    for stale in out_dir.glob("messages*.html"):
        if stale.name not in written and re.fullmatch(
                r"messages\d*\.html", stale.name):
            stale.unlink()
    return written


# ---------------------------------------------------------------------------
# Fuse
# ---------------------------------------------------------------------------

def chat_title(header_html: str):
    m = re.search(r'<div class="text bold">\s*\n(.*?)\n', header_html)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else None


def fuse(export_dirs, out_dir: Path, page_bytes: float, force=False):
    per_export = []
    for i, d in enumerate(export_dirs):
        report_stage("scan", frac=i / len(export_dirs), name=d.name)
        per_export.append(parse_export(d))

    # safety: refuse to silently merge exports of different chats
    titles = {t for t in (chat_title(h) for _, h, _ in per_export) if t}
    if len(titles) > 1 and not force:
        raise ValueError(
            "Los exports parecen de chats distintos: "
            + " · ".join(sorted(titles))
            + ". Usa --force (o confirma en la interfaz) para fusionarlos "
              "igualmente.")

    report_stage("merge")

    # sender header templates, later exports win
    sender_headers = {}
    for _, _, headers in per_export:
        sender_headers.update(headers)

    merged = merge_messages(per_export)
    real = [m for m in merged if m.msg_id > 0]
    print(f"Merged {sum(len(p[0]) for p in per_export)} parsed messages "
          f"from {len(export_dirs)} exports -> {len(real)} unique messages")
    if real:
        print(f"Range: {real[0].date} .. {real[-1].date}")

    out_dir.mkdir(parents=True, exist_ok=True)

    # static assets from the first export
    for asset in ASSET_DIRS:
        src = export_dirs[0] / asset
        if src.is_dir():
            shutil.copytree(src, out_dir / asset, dirs_exist_ok=True)

    # per-message preparation: repair merge-orphaned joined messages,
    # copy media, normalize reply links (repaginated later)
    copied = {}
    prev_real = None
    for i, msg in enumerate(merged):
        if i % 25 == 0:
            report_stage("media", frac=i / max(len(merged), 1),
                         copied=len(copied))
        if msg.msg_id > 0:
            if msg.joined and (prev_real is None
                               or prev_real.sender != msg.sender
                               or prev_real.date is None or msg.date is None
                               or prev_real.date.date() != msg.date.date()):
                msg.html = unjoin(msg, sender_headers)
                msg.joined = False
            prev_real = msg
        msg.html = (copy_media(msg, out_dir, copied)
                    if media_maps is None else apply_media_map(msg))

    written = write_pages(merged, per_export[0][1], out_dir, page_bytes,
                          sender_headers)
    print(f"Wrote {len(written)} page(s) in {out_dir}: "
          f"{written[0]} .. {written[-1]}" if len(written) > 1 else
          f"Wrote {out_dir / written[0]}")
    return {
        "messages": len(real),
        "range": [str(real[0].date), str(real[-1].date)] if real else None,
        "pages": written,
        "out_dir": str(out_dir),
    }


def order_exports(export_dirs):
    """Oldest export first, so newer exports override edited messages."""
    return sorted(export_dirs, key=lambda d: max(
        (int(m.group(1)) for p in d.glob("messages*.html")
         for m in MSG_OPEN_RE.finditer(p.read_text(encoding="utf-8"))
         if int(m.group(1)) > 0), default=0))


def main():
    ap = argparse.ArgumentParser(
        description="Merge multiple Telegram HTML chat exports into one.")
    ap.add_argument("exports", nargs="+",
                    help="Telegram export folders (each contains messages.html)")
    ap.add_argument("-o", "--output", default="ChatExport_merged",
                    help="output folder (default: ChatExport_merged)")
    ap.add_argument("-s", "--page-size", default=DEFAULT_PAGE_SIZE,
                    help="approximate size of each messagesN.html page, "
                         "e.g. 500KB, 2MB; 0 = single file "
                         f"(default: {DEFAULT_PAGE_SIZE})")
    ap.add_argument("-f", "--force", action="store_true",
                    help="merge even if the exports look like different "
                         "chats (mismatching chat titles)")
    args = ap.parse_args()

    export_dirs = [Path(p).resolve() for p in args.exports]
    for d in export_dirs:
        if not d.is_dir():
            sys.exit(f"error: {d} is not a directory")
    out_dir = Path(args.output).resolve()
    if out_dir in export_dirs:
        sys.exit("error: output folder cannot be one of the input exports")

    try:
        fuse(order_exports(export_dirs), out_dir,
             parse_size(args.page_size), force=args.force)
    except ValueError as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
