#!/usr/bin/env python3
"""Telegram Export Enhancer — makes an HTML export look like real Telegram.

Every enhancement is an independent, optional feature:

  bubbles  chat bubbles over a Telegram-style background, sticky header,
           date pills and the layout modes ("original" full width /
           "chat" with own messages on the right / "both" switchable)
  quotes   replies shown as a quote box (colored bar + author + snippet)
           instead of "In reply to this message", working across pages
  theme    floating light/dark toggle (also works on the original design)
  media    videos and voice/audio messages play inline; photos open in a
           lightbox instead of a new tab
  note     a final note on the last page explaining how to run these
           tools on Windows, macOS and Linux

The transformation is idempotent AND reversible per feature: re-running
with a feature disabled restores that part of the original markup, so
you can change your mind at any time. Fusing and compacting an enhanced
export keeps working. Media files are never touched.

Usage:
    python telegram_export_enhancer.py <export_dir> [--me "Tu Nombre"]
        [--layout both|chat|original] [--no-bubbles] [--no-quotes]
        [--no-theme] [--no-media] [--no-note]
"""

import argparse
import json
import re
import sys
from pathlib import Path

from telegram_export_fuser import FROM_NAME_RE, MSG_OPEN_RE, report_stage

ALL_FEATURES = ("bubbles", "quotes", "theme", "media", "note")
VIDEO_EXTS = (".mp4", ".webm", ".mov", ".m4v")

# URL del proyecto: se muestra como enlace discreto en la nota final.
REPO_URL = "https://github.com/Marcos-SA-git/Telegram-Export-Studio"

REPLY_RE = re.compile(
    r'<div class="reply_to details">\s*In reply to '
    r'<a href="[^"]*?#go_to_message(\d+)"[^>]*>this message</a>\s*</div>')
VIDEO_RE = re.compile(
    r'<a class="video_file_wrap[^"]*" href="([^"]+)">.*?'
    r'<img class="video_file" src="([^"]+)"( style="[^"]*")?/>\s*</a>',
    re.DOTALL)
AUDIO_RE = re.compile(
    r'<a class="media clearfix pull_left block_link '
    r'(media_voice_message|media_audio_file)" href="([^"]+)">.*?</a>',
    re.DOTALL)
DURATION_RE = re.compile(r'<div class="video_duration">\s*\n(.*?)\n', re.DOTALL)
STATUS_RE = re.compile(r'<div class="status details">\s*\n(.*?)\n', re.DOTALL)
MSG_CLASS_RE = re.compile(
    r'<div class="message default clearfix((?: joined)?)(?: out)?" '
    r'id="(message\d+)">')

# reverse patterns — they match exactly what the forward transforms emit
QUOTE_LIVE_RE = re.compile(
    r'<div class="reply_to details"><a class="reply_quote" '
    r'href="(?:(messages\d*\.html))?#go_to_message(\d+)"[^>]*>.*?</a></div>',
    re.DOTALL)
QUOTE_DEAD_RE = re.compile(
    r'<div class="reply_to details"><span class="reply_quote rq_dead"'
    r'(?: data-mid="(\d+)")?>.*?</span></div>', re.DOTALL)
TG_VIDEO_RE = re.compile(
    r'<div class="video_file_wrap clearfix pull_left tg_video"'
    r'(?: data-duration="([^"]*)")?>'
    r'<video class="video_file" controls preload="metadata" '
    r'poster="([^"]+)"( style="[^"]*")?><source src="([^"]+)"/></video>'
    r'</div>')
TG_AUDIO_RE = re.compile(
    r'<div class="media clearfix pull_left (tg_voice|tg_audio)"'
    r'(?: data-status="([^"]*)")?>.*?<audio controls preload="metadata" '
    r'src="([^"]+)"></audio></div>', re.DOTALL)

NOTE_RE = re.compile(r'\n*<div class="tg_enhanced_note">.*?<!--/tg_note-->',
                     re.DOTALL)
CONFIG_RE = re.compile(r'<script id="tg-enhanced-config">.*?</script>',
                       re.DOTALL)
FOOT_RE = re.compile(r'(\n    </div>\n\n   </div>\n\n  </div>\n\n </body>)')


def sorted_pages(export_dir: Path):
    return sorted(export_dir.glob("messages*.html"),
                  key=lambda p: int(re.search(r"(\d*)\.html$", p.name)
                                    .group(1) or 0))


def iter_blocks(html: str):
    """Yield (start, end, block) for each top-level message div."""
    div_re = re.compile(r"<div\b|</div>")
    for m in MSG_OPEN_RE.finditer(html):
        depth = 0
        for d in div_re.finditer(html, m.start()):
            depth += 1 if d.group() != "</div>" else -1
            if depth == 0:
                end = html.find(">", d.start()) + 1
                yield m.start(), end, html[m.start():end]
                break


def snippet_of(block: str) -> str:
    tm = re.search(r'<div class="text">\s*\n(.*?)\n\s*</div>', block,
                   re.DOTALL)
    if tm:
        text = re.sub(r"<[^>]+>", "", tm.group(1))
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            return text[:87] + "…" if len(text) > 88 else text
    if "photo_wrap" in block:
        return "📷 Foto"
    if "video_file_wrap" in block or "tg_video" in block:
        return "📹 Vídeo"
    if "media_voice_message" in block or "tg_voice" in block:
        return "🎤 Mensaje de voz"
    if "media_audio_file" in block or "tg_audio" in block:
        return "🎵 Audio"
    if "media_file" in block:
        return "📎 Archivo"
    if "sticker" in block.lower():
        return "Sticker"
    return "Mensaje"


def build_index(pages):
    """id -> {sender, snippet, page} for every real message."""
    index = {}
    for page in pages:
        html = page.read_text(encoding="utf-8")
        current_sender = None
        for _, _, block in iter_blocks(html):
            msg_id = int(MSG_OPEN_RE.search(block).group(1))
            if msg_id < 0 or 'class="message service"' in block:
                continue
            fm = FROM_NAME_RE.search(block)
            if fm:
                current_sender = fm.group(1).strip()
            index[msg_id] = {
                "sender": current_sender or "…",
                "snippet": snippet_of(block),
                "page": page.name,
            }
    return index


# ---------------------------------------------------------------------------
# Reversible transforms. Each block is first normalized back to original
# Telegram markup and then the enabled features are re-applied — this makes
# every feature idempotent, refreshable and individually removable.
# ---------------------------------------------------------------------------

def revert_quotes(block: str) -> str:
    def live(m):
        page, target = m.group(1), int(m.group(2))
        if page:
            a = f'<a href="{page}#go_to_message{target}">'
        else:
            a = (f'<a href="#go_to_message{target}" '
                 f'onclick="return GoToMessage({target})">')
        return (f'<div class="reply_to details">\nIn reply to {a}this '
                f'message</a>\n       </div>')

    def dead(m):
        if not m.group(1):
            return m.group(0)  # legacy dead quote without id: keep
        t = int(m.group(1))
        return (f'<div class="reply_to details">\nIn reply to '
                f'<a href="#go_to_message{t}" onclick="return '
                f'GoToMessage({t})">this message</a>\n       </div>')

    block = QUOTE_LIVE_RE.sub(live, block)
    return QUOTE_DEAD_RE.sub(dead, block)


def revert_media(block: str) -> str:
    def video(m):
        dur, poster, style, src = (m.group(1), m.group(2),
                                   m.group(3) or "", m.group(4))
        dur_div = (f'<div class="video_duration">\n{dur}\n</div>'
                   if dur else "")
        return (f'<a class="video_file_wrap clearfix pull_left" '
                f'href="{src}"><div class="video_play_bg">'
                f'<div class="video_play"></div></div>{dur_div}'
                f'<img class="video_file" src="{poster}"{style}/></a>')

    def audio(m):
        css, status, src = m.group(1), m.group(2), m.group(3)
        kind = ("media_voice_message" if css == "tg_voice"
                else "media_audio_file")
        title = "Voice message" if css == "tg_voice" else "Audio file"
        status_div = (f'<div class="status details">\n{status}\n</div>'
                      if status else "")
        return (f'<a class="media clearfix pull_left block_link {kind}" '
                f'href="{src}"><div class="fill pull_left"></div>'
                f'<div class="body"><div class="title bold">\n{title}\n'
                f'</div>{status_div}</div></a>')

    block = TG_VIDEO_RE.sub(video, block)
    return TG_AUDIO_RE.sub(audio, block)


def apply_quotes(block, index, page_name):
    def repl(m):
        target = int(m.group(1))
        info = index.get(target)
        if info is None:
            inner = (f'<span class="reply_quote rq_dead" data-mid="{target}">'
                     '<span class="rq_author">Mensaje no disponible</span>'
                     '<span class="rq_snippet">No está incluido en el '
                     'export</span></span>')
        else:
            if info["page"] == page_name:
                a = (f'<a class="reply_quote" href="#go_to_message{target}" '
                     f'onclick="return GoToMessage({target})">')
            else:
                a = (f'<a class="reply_quote" '
                     f'href="{info["page"]}#go_to_message{target}">')
            inner = (f'{a}<span class="rq_author">{info["sender"]}</span>'
                     f'<span class="rq_snippet">{info["snippet"]}</span></a>')
        return f'<div class="reply_to details">{inner}</div>'

    return REPLY_RE.sub(repl, block)


def apply_media(block):
    def video(m):
        href, thumb, style = m.group(1), m.group(2), m.group(3) or ""
        if not href.lower().endswith(VIDEO_EXTS):
            return m.group(0)
        dm = DURATION_RE.search(m.group(0))
        dur = f' data-duration="{dm.group(1).strip()}"' if dm else ""
        return (f'<div class="video_file_wrap clearfix pull_left tg_video"'
                f'{dur}><video class="video_file" controls '
                f'preload="metadata" poster="{thumb}"{style}>'
                f'<source src="{href}"/></video></div>')

    def audio(m):
        kind, href = m.group(1), m.group(2)
        css = "tg_voice" if kind == "media_voice_message" else "tg_audio"
        label = ("🎤 Mensaje de voz" if kind == "media_voice_message"
                 else "🎵 Audio")
        sm = STATUS_RE.search(m.group(0))
        status = f' data-status="{sm.group(1).strip()}"' if sm else ""
        return (f'<div class="media clearfix pull_left {css}"{status}>'
                f'<div class="tg_media_label">{label}</div>'
                f'<audio controls preload="metadata" src="{href}"></audio>'
                f'</div>')

    block = VIDEO_RE.sub(video, block)
    return AUDIO_RE.sub(audio, block)


def transform_block(block, sender, me, index, page_name, features):
    # own-message tagging (idempotent: 'out' stripped and re-added)
    is_out = me is not None and sender == me
    block = MSG_CLASS_RE.sub(
        lambda m: (f'<div class="message default clearfix{m.group(1)}'
                   f'{" out" if is_out else ""}" id="{m.group(2)}">'),
        block, count=1)

    # normalize back to original markup, then re-apply enabled features
    block = revert_quotes(block)
    block = revert_media(block)
    if features["quotes"]:
        block = apply_quotes(block, index, page_name)
    if features["media"]:
        block = apply_media(block)
    return block


def enhance(export_dir, me=None, layout="both", features=None,
            fullwidth=True):
    d = Path(export_dir).resolve()
    pages = sorted_pages(d)
    if not pages:
        raise ValueError(f"No se encontró messages.html en {d}")
    if layout not in ("both", "chat", "original"):
        raise ValueError(f"layout inválido: {layout}")
    feats = {k: True for k in ALL_FEATURES}
    feats.update(features or {})
    if feats["bubbles"] and layout != "original" and not me:
        raise ValueError(
            "Para el modo chat hay que indicar quién eres tú (--me)")

    report_stage("scan", name=d.name)
    index = build_index(pages)
    title = None
    n_out = 0

    for pi, page in enumerate(pages):
        report_stage("enhance", frac=pi / len(pages), name=page.name)
        html = page.read_text(encoding="utf-8")
        if title is None:
            tm = re.search(r'<div class="text bold">\s*\n(.*?)\n', html)
            title = tm.group(1).strip() if tm else ""

        # rebuild page from transformed blocks
        out_parts = []
        pos = 0
        current_sender = None
        for start, end, block in iter_blocks(html):
            msg_id = int(MSG_OPEN_RE.search(block).group(1))
            if msg_id > 0 and 'class="message service"' not in block:
                fm = FROM_NAME_RE.search(block)
                if fm:
                    current_sender = fm.group(1).strip()
                new = transform_block(block, current_sender, me, index,
                                      page.name, feats)
                if ' out"' in new.split(">", 1)[0]:
                    n_out += 1
            else:
                new = block
            out_parts.append(html[pos:start])
            out_parts.append(new)
            pos = end
        out_parts.append(html[pos:])
        html = "".join(out_parts)

        # injections (idempotent)
        if 'css/enhanced.css' not in html:
            html = html.replace(
                '<link href="css/style.css" rel="stylesheet"/>',
                '<link href="css/style.css" rel="stylesheet"/>\n'
                '<link href="css/enhanced.css" rel="stylesheet"/>', 1)
        config = ('<script id="tg-enhanced-config">window.TG_ENHANCED = '
                  + json.dumps({"me": me, "layout": layout, "title": title,
                                "fullwidth": bool(fullwidth),
                                "features": feats}, ensure_ascii=False)
                  + ';</script>')
        if CONFIG_RE.search(html):
            html = CONFIG_RE.sub(lambda m: config, html, count=1)
        else:
            html = html.replace(
                "</head>", config +
                '\n<script src="js/enhanced.js"></script>\n</head>', 1)

        html = NOTE_RE.sub("", html)
        if feats["note"] and pi == len(pages) - 1:
            if FOOT_RE.search(html):
                html = FOOT_RE.sub("\n" + NOTE_HTML + r"\1", html, count=1)
            else:
                html = html.replace("</body>",
                                    NOTE_HTML + "\n</body>", 1)

        page.write_text(html, encoding="utf-8")

    (d / "css").mkdir(exist_ok=True)
    (d / "js").mkdir(exist_ok=True)
    (d / "css" / "enhanced.css").write_text(ENHANCED_CSS, encoding="utf-8")
    (d / "js" / "enhanced.js").write_text(ENHANCED_JS, encoding="utf-8")

    active = [k for k in ALL_FEATURES if feats[k]]
    print(f"Mejoradas {len(pages)} página(s) en {d}")
    print(f"Opciones activas: {', '.join(active) or 'ninguna'}")
    print(f"Mensajes propios ('{me}'): {n_out}" if me else
          "Sin remitente propio")
    return {
        "messages": len(index),
        "pages": [p.name for p in pages],
        "out_dir": str(d),
        "own": n_out,
        "features": feats,
    }


def restore(export_dir):
    """Undo every enhancement: revert quotes and inline media to the
    original Telegram markup, drop the 'out' tagging, remove the injected
    CSS/JS/config/note and delete the enhanced asset files."""
    d = Path(export_dir).resolve()
    pages = sorted_pages(d)
    if not pages:
        raise ValueError(f"No se encontró messages.html en {d}")

    report_stage("scan", name=d.name)
    n_msgs = 0
    for pi, page in enumerate(pages):
        report_stage("restore", frac=pi / len(pages), name=page.name)
        html = page.read_text(encoding="utf-8")

        out_parts = []
        pos = 0
        for start, end, block in iter_blocks(html):
            msg_id = int(MSG_OPEN_RE.search(block).group(1))
            if msg_id > 0 and 'class="message service"' not in block:
                n_msgs += 1
                block = MSG_CLASS_RE.sub(
                    lambda m: (f'<div class="message default clearfix'
                               f'{m.group(1)}" id="{m.group(2)}">'),
                    block, count=1)
                block = revert_quotes(block)
                block = revert_media(block)
            out_parts.append(html[pos:start])
            out_parts.append(block)
            pos = end
        out_parts.append(html[pos:])
        html = "".join(out_parts)

        html = html.replace(
            '\n<link href="css/enhanced.css" rel="stylesheet"/>', "")
        html = CONFIG_RE.sub("", html)
        html = html.replace('\n<script src="js/enhanced.js"></script>', "")
        html = NOTE_RE.sub("", html)
        page.write_text(html, encoding="utf-8")

    for asset in (d / "css" / "enhanced.css", d / "js" / "enhanced.js"):
        if asset.is_file():
            asset.unlink()

    print(f"Restauradas {len(pages)} página(s) en {d} al diseño original")
    return {
        "messages": n_msgs,
        "pages": [p.name for p in pages],
        "out_dir": str(d),
        "own": 0,
    }


# ---------------------------------------------------------------------------
# Injected assets
# ---------------------------------------------------------------------------

NOTE_HTML = f"""\
<div class="tg_enhanced_note">
 <div class="tg_note_card">
  <div class="tg_note_head">✨ Export procesado con Telegram Export Studio</div>
  <div class="tg_note_text">Historial fusionado y mejorado 100% en local con
   scripts de Python. Nada sale de tu equipo.</div>
  <details>
   <summary>Cómo usar las herramientas (Windows · macOS · Linux)</summary>
   <ol>
    <li>Instala <b>Python 3.10 o superior</b> desde
     <a href="https://www.python.org/downloads/">python.org</a>
     (en macOS y Linux suele venir preinstalado).</li>
    <li>Copia los cuatro scripts a una carpeta:
     <code>telegram_export_studio.py</code>,
     <code>telegram_export_fuser.py</code>,
     <code>telegram_export_compactor.py</code> y
     <code>telegram_export_enhancer.py</code>.</li>
    <li>Abre una terminal en esa carpeta y ejecuta la interfaz gráfica:<br>
     <code>Windows&nbsp;&nbsp;&nbsp;·&nbsp; py telegram_export_studio.py</code><br>
     <code>macOS/Linux&nbsp;·&nbsp; python3 telegram_export_studio.py</code></li>
    <li>Desde el navegador podrás <b>fusionar</b> varios exports,
     <b>compactar</b> sus páginas y <b>mejorar</b> la visualización.
     También hay línea de comandos, por ejemplo:<br>
     <code>python telegram_export_fuser.py export1 export2 -o fusionado</code></li>
   </ol>
  </details>
  <div class="tg_note_links">
   <a href="{REPO_URL}" target="_blank" rel="noopener">
    <svg viewBox="0 0 16 16" width="13" height="13" fill="currentColor"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82a7.42 7.42 0 0 1 4 0c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z"/></svg>
    Código abierto · ver el proyecto en GitHub</a>
  </div>
 </div>
</div><!--/tg_note-->"""


ENHANCED_CSS = r"""/* Telegram Export Studio — enhanced viewer
   Classes live on <html> (documentElement), so they apply from the very
   first paint even though the script is loaded in <head>. */
.tg-enhanced {
  --tg-accent: #3390ec;
  --tg-radius: 15px;
  --tg-text: #0e1621;
  --tg-time: #99a6b1;
  --tg-quote-bg: rgba(51, 144, 236, .09);
  --tg-note-bg: rgba(255, 255, 255, .92);
  --tg-bubble-shadow: 0 1px 1.5px rgba(16, 35, 47, .18);
  --tg-ctl-bg: rgba(255, 255, 255, .88);
  --tg-ctl-fg: #40525f;
}
.tg-enhanced.tg-dark {
  --tg-accent: #6ab3f3;
  --tg-text: #f1f5f8;
  --tg-time: #6d8195;
  --tg-quote-bg: rgba(106, 179, 243, .16);
  --tg-note-bg: rgba(23, 33, 43, .92);
  --tg-bubble-shadow: 0 1px 1.5px rgba(0, 0, 0, .4);
  --tg-ctl-bg: rgba(30, 42, 55, .88);
  --tg-ctl-fg: #c8d6e2;
}

/* ==== dark theme for the ORIGINAL design (no bubbles) ==== */
.tg-dark:not(.tg-bubbles), .tg-dark:not(.tg-bubbles) body {
  background: #0e1621; }
.tg-dark:not(.tg-bubbles) .page_wrap { color: #e8edf2; }
.tg-dark:not(.tg-bubbles) .page_header {
  background: #17212b; border-color: #101921; }
.tg-dark:not(.tg-bubbles) .page_header .content .text.bold { color: #f1f5f8; }
.tg-dark:not(.tg-bubbles) .page_body { color: #e8edf2; }
.tg-dark:not(.tg-bubbles) .text { color: #e8edf2; }
.tg-dark:not(.tg-bubbles) .from_name { color: #6ab3f3; }
.tg-dark:not(.tg-bubbles) .details { color: #6d8195; }
.tg-dark:not(.tg-bubbles) a { color: #6ab3f3; }
.tg-dark:not(.tg-bubbles) .message.service .body.details { color: #aebbc9; }

/* ==== bubbles + chat background (feature: bubbles) ==== */
.tg-bubbles {
  --tg-header-bg: rgba(255, 255, 255, .82);
  --tg-header-text: #1c2733;
  --tg-in-bg: #ffffff;
  --tg-out-bg: #e9fcd4;
  --tg-time-out: #62ac55;
  --tg-name: #2f7cc0;
  --tg-service-bg: rgba(255, 255, 255, .88);
  --tg-service-text: #5d7285;
}
.tg-bubbles body {
  margin: 0;
  font-family: "Segoe UI", -apple-system, "SF Pro Text", Roboto,
               "Helvetica Neue", sans-serif;
  background: linear-gradient(180deg, #d9e7f0, #c6dbea);
  background-attachment: fixed;
}
.tg-bubbles.tg-dark {
  --tg-header-bg: rgba(23, 33, 43, .85);
  --tg-header-text: #f1f5f8;
  --tg-in-bg: #182533;
  --tg-out-bg: #2b5278;
  --tg-time-out: #7da8d3;
  --tg-name: #6ab3f3;
  --tg-service-bg: rgba(14, 22, 33, .7);
  --tg-service-text: #aebbc9;
}
.tg-bubbles.tg-dark body {
  background: linear-gradient(180deg, #14212c, #0e1621);
}
/* width: a comfortable centered column by default; the separate
   "full width" option (html.tg-fullwidth) removes it so the chat spans
   the whole screen (style.css pins .page_body to 480px) */
.tg-bubbles .page_wrap {
  background: transparent; box-shadow: none;
  max-width: none; margin: 0; color: var(--tg-text);
}
.tg-bubbles .page_body {
  width: auto; max-width: 720px; margin: 0 auto; padding-top: 0;
}
/* independent of bubbles: also un-pins the 480px column of the
   original design */
.tg-fullwidth .page_body { width: auto; max-width: none; margin: 0; }
.tg-bubbles .page_header .content { width: auto; }
.tg-bubbles .page_header {
  position: sticky; top: 0; z-index: 40;
  background: var(--tg-header-bg); backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border-radius: 0 0 14px 14px; border-bottom: none;
}
.tg-bubbles .page_header .content .text.bold {
  color: var(--tg-header-text); font-size: 15px; padding: 12px 20px;
}
.tg-bubbles .page_body { background: transparent; }
.tg-bubbles .history { padding: 14px 16px 30px; }

.tg-bubbles .message.default {
  display: flex; align-items: flex-end; gap: 8px;
  margin: 3px 6px; padding: 0; border: 0; overflow: visible;
}
.tg-bubbles .message.default > .userpic_wrap { float: none; flex: none; }
.tg-bubbles .message.default > .body {
  background: var(--tg-in-bg); color: var(--tg-text);
  border-radius: var(--tg-radius);
  padding: 6px 12px 8px; margin: 0;
  box-shadow: var(--tg-bubble-shadow);
  position: relative; min-width: 90px; max-width: 100%;
}
.tg-bubbles .message.default.joined { margin-top: 1px; }
.tg-bubbles.layout-original .message.joined,
.tg-bubbles.layout-chat .message.joined:not(.out) { margin-left: 56px; }
.tg-bubbles .message.joined > .body { border-top-left-radius: 7px; }

.tg-bubbles .text {
  font-size: 14.5px; line-height: 1.38; color: var(--tg-text);
  word-wrap: break-word;
}
.tg-bubbles .text a { color: var(--tg-accent); }
.tg-bubbles .from_name {
  color: var(--tg-name); font-weight: 600; font-size: 13.5px;
  padding-bottom: 2px;
}
.tg-bubbles .pull_right.date.details {
  float: right; font-size: 11.5px; color: var(--tg-time);
  margin: 12px -4px -4px 10px; user-select: none;
}
.tg-bubbles .message.selected > .body { outline: 2px solid var(--tg-accent); }

/* your own messages are always highlighted, in every layout */
.tg-bubbles .message.default.out > .body { background: var(--tg-out-bg); }
.tg-bubbles .message.default.out .pull_right.date.details {
  color: var(--tg-time-out);
}
/* "original" layout: every avatar on the left, like the original export */
.tg-bubbles.layout-original .message.default > .body { flex: 1; }
/* "chat" layout: their avatar+bubble left, YOUR avatar+bubble right */
.tg-bubbles.layout-chat .message.default > .body { max-width: min(560px, 78%); }
.tg-bubbles.layout-chat .message.default.out {
  flex-direction: row-reverse; }
.tg-bubbles.layout-chat .message.default.out .from_name { display: none; }
.tg-bubbles.layout-chat .message.default.out.joined { margin-right: 56px; }
.tg-bubbles.layout-chat .message.default.out.joined > .body {
  border-top-left-radius: var(--tg-radius); border-top-right-radius: 7px;
}

.tg-bubbles .message.service {
  display: flex; justify-content: center; margin: 16px 0; border: 0;
}
.tg-bubbles .message.service .body.details {
  background: var(--tg-service-bg); color: var(--tg-service-text);
  border-radius: 999px; padding: 5px 14px;
  font-size: 12.5px; font-weight: 600; width: auto;
  backdrop-filter: blur(6px); -webkit-backdrop-filter: blur(6px);
  box-shadow: var(--tg-bubble-shadow);
}
.tg-bubbles .photo_wrap { float: none; display: inline-block; }
.tg-bubbles .photo_wrap img.photo {
  border-radius: 10px; max-width: 100%; height: auto; display: block;
}
.tg-bubbles .media_wrap { margin-top: 4px; }
.tg-bubbles .media.clearfix { border: 0; }

/* ==== calls: flat row inside the bubble, status icon via JS ==== */
.tg-bubbles .media.media_call {
  float: none; display: flex; align-items: center; gap: 12px;
  background: none; border: 0; padding: 2px 0; margin: 0;
}
.tg-bubbles .media.media_call .fill { display: none; }
.tg-bubbles .media.media_call .body {
  background: none; box-shadow: none; padding: 0; margin: 0; min-width: 0;
}
.tg-bubbles .media.media_call .title {
  font-weight: 600; font-size: 13.5px; color: var(--tg-text);
}
.tg-bubbles .media.media_call .status {
  font-size: 12.5px; color: var(--tg-time);
}
.tg_call_icon {
  width: 36px; height: 36px; border-radius: 50%; flex: none;
  display: grid; place-items: center;
}
.tg_call_icon svg { width: 19px; height: 19px; fill: #fff; }
.tg_call_icon.ok { background: #4fae4e; }
.tg_call_icon.bad { background: #e05a4e; }
.tg_call_icon.bad svg { transform: rotate(135deg); }

/* ==== reply quote (feature: quotes) ==== */
.tg-enhanced .reply_to.details { margin: 2px 0 5px; padding: 0; }
.reply_quote {
  display: block; text-decoration: none;
  background: var(--tg-quote-bg, rgba(51,144,236,.09));
  border-left: 3px solid var(--tg-accent, #3390ec);
  border-radius: 5px 8px 8px 5px;
  padding: 3px 9px 4px 7px; cursor: pointer;
  transition: filter .15s;
}
a.reply_quote:hover { filter: brightness(1.06); text-decoration: none; }
.reply_quote .rq_author {
  display: block; color: var(--tg-accent, #3390ec);
  font-size: 12.5px; font-weight: 600; line-height: 1.35;
}
.reply_quote .rq_snippet {
  display: block; color: inherit; opacity: .82;
  font-size: 12.5px; line-height: 1.35;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  max-width: 340px;
}
.tg-dark .reply_quote .rq_snippet { color: #dbe5ee; }
.reply_quote.rq_dead { cursor: default; opacity: .7; }

/* ==== pagination link between pages ==== */
.tg-enhanced a.pagination {
  display: block; width: fit-content; margin: 20px auto 8px;
  padding: 10px 26px; border-radius: 999px;
  background: var(--tg-note-bg); color: var(--tg-accent);
  font-weight: 600; font-size: 14px; text-decoration: none;
  box-shadow: var(--tg-bubble-shadow);
  backdrop-filter: blur(8px); -webkit-backdrop-filter: blur(8px);
}
.tg-enhanced a.pagination:hover { filter: brightness(1.05); }

/* ==== inline media (feature: media) ==== */
.tg_video { float: none; display: inline-block; max-width: 100%; }
.tg_video video {
  border-radius: 10px; max-width: 100%; height: auto;
  background: #000; display: block; outline: none;
}
.tg_voice, .tg_audio { float: none; display: block; padding: 2px 0; }
.tg_media_label {
  font-size: 12.5px; font-weight: 600; color: var(--tg-accent, #3390ec);
  padding: 2px 0 4px;
}
.tg_voice audio, .tg_audio audio {
  display: block; width: min(300px, 68vw); height: 38px; outline: none;
}
.tg-enhanced .photo_wrap img.photo { cursor: zoom-in; }

/* ==== floating controls ==== */
.tg_controls {
  position: fixed; top: 12px; right: 14px; z-index: 90;
  display: flex; gap: 8px;
}
.tg_ctl {
  width: 42px; height: 42px; border-radius: 50%; border: 0; cursor: pointer;
  background: var(--tg-ctl-bg); color: var(--tg-ctl-fg);
  backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px);
  box-shadow: 0 2px 10px rgba(0, 0, 0, .22);
  display: grid; place-items: center;
  transition: transform .15s;
}
.tg_ctl:hover { transform: scale(1.09); }
.tg_ctl svg { width: 21px; height: 21px; fill: currentColor; }

/* ==== lightbox ==== */
.tg_lightbox {
  position: fixed; inset: 0; z-index: 200;
  background: rgba(5, 10, 16, .93);
  display: grid; place-items: center; cursor: zoom-out;
  animation: tgfade .18s ease;
}
@keyframes tgfade { from { opacity: 0; } }
.tg_lightbox img {
  max-width: 94vw; max-height: 94vh; border-radius: 8px;
  box-shadow: 0 12px 60px rgba(0, 0, 0, .6);
}

/* ==== end note (feature: note) ==== */
.tg_enhanced_note { display: flex; justify-content: center; margin: 26px 6px 10px; }
.tg_note_card {
  background: var(--tg-note-bg, rgba(255,255,255,.92));
  color: var(--tg-text, #0e1621);
  border-radius: 16px; padding: 16px 20px; max-width: 480px;
  font-size: 13px; line-height: 1.5;
  box-shadow: var(--tg-bubble-shadow, 0 1px 2px rgba(0,0,0,.15));
  backdrop-filter: blur(8px); -webkit-backdrop-filter: blur(8px);
}
.tg_note_head { font-weight: 700; font-size: 13.5px; margin-bottom: 4px; }
.tg_note_text { opacity: .8; }
.tg_note_card details { margin-top: 8px; }
.tg_note_card summary {
  cursor: pointer; color: var(--tg-accent, #3390ec); font-weight: 600;
}
.tg_note_card ol { margin: 8px 0 0; padding-left: 20px; }
.tg_note_card li { margin-bottom: 6px; }
.tg_note_card code {
  background: rgba(128, 148, 168, .16); border-radius: 5px;
  padding: 1px 6px; font-size: 12px;
  font-family: Consolas, "SF Mono", monospace;
}
.tg_note_card a { color: var(--tg-accent, #3390ec); }
.tg_note_links { margin-top: 10px; font-size: 12px; }
.tg_note_links a { display: inline-flex; align-items: center; gap: 5px;
  opacity: .65; text-decoration: none; transition: opacity .2s; }
.tg_note_links a:hover { opacity: 1; text-decoration: underline; }
"""


ENHANCED_JS = r"""// Telegram Export Studio — enhanced viewer
// Loaded from <head>: theme/layout classes go on <html> immediately (no
// flash of unstyled content); the control bar waits for DOMContentLoaded.
(function () {
  "use strict";
  var cfg = window.TG_ENHANCED || {};
  var f = cfg.features || { bubbles: true, quotes: true, theme: true,
                            media: true, note: true };
  var KEY = "tgx-" + (cfg.title || "chat");

  function store(k, v) {
    try {
      if (v === undefined) return localStorage.getItem(KEY + ":" + k);
      localStorage.setItem(KEY + ":" + k, v);
    } catch (e) { return null; }
  }

  var root = document.documentElement;
  root.classList.add("tg-enhanced");
  if (f.bubbles) root.classList.add("tg-bubbles");
  if (cfg.fullwidth !== false) root.classList.add("tg-fullwidth");

  var theme = "light";
  if (f.theme) {
    theme = store("theme") ||
      (window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark" : "light");
  }
  var layout = cfg.layout === "both"
    ? (store("layout") || "chat")
    : (cfg.layout || "original");
  root.classList.add("tg-" + theme, "layout-" + layout);

  var ICONS = {
    sun: '<svg viewBox="0 0 24 24"><path d="M12 7a5 5 0 1 0 0 10 5 5 0 0 0 0-10zm0-6h0l1 3.5h-2L12 1zm0 22-1-3.5h2L12 23zM1 12l3.5-1v2L1 12zm22 0-3.5 1v-2L23 12zM4.2 4.2l3.2 1.8-1.4 1.4L4.2 4.2zm15.6 15.6-3.2-1.8 1.4-1.4 1.8 3.2zM19.8 4.2 18 7.4l-1.4-1.4 3.2-1.8zM4.2 19.8 6 16.6l1.4 1.4-3.2 1.8z"/></svg>',
    moon: '<svg viewBox="0 0 24 24"><path d="M12.3 2c.3 0 .7 0 1 .1a8.5 8.5 0 0 0 8.6 12.4A10 10 0 1 1 12.3 2z"/></svg>',
    wide: '<svg viewBox="0 0 24 24"><path d="M3 5h18v3H3V5zm0 5.5h18v3H3v-3zM3 16h18v3H3v-3z"/></svg>',
    chat: '<svg viewBox="0 0 24 24"><path d="M3 5h12v3H3V5zm6 5.5h12v3H9v-3zM3 16h12v3H3v-3z"/></svg>'
  };

  function init() {
    function mkBtn(title, html, onclick) {
      var b = document.createElement("button");
      b.className = "tg_ctl";
      b.title = title;
      b.innerHTML = html;
      b.onclick = onclick;
      return b;
    }

    var bar = document.createElement("div");
    bar.className = "tg_controls";

    if (f.theme) {
      var themeBtn = mkBtn("Cambiar tema",
        root.classList.contains("tg-dark") ? ICONS.sun : ICONS.moon,
        function () {
          var next = root.classList.contains("tg-dark") ? "light" : "dark";
          root.classList.remove("tg-dark", "tg-light");
          root.classList.add("tg-" + next);
          themeBtn.innerHTML = next === "dark" ? ICONS.sun : ICONS.moon;
          store("theme", next);
        });
      bar.appendChild(themeBtn);
    }

    if (f.bubbles && cfg.layout === "both") {
      var layoutBtn = mkBtn("Cambiar disposición",
        root.classList.contains("layout-chat") ? ICONS.wide : ICONS.chat,
        function () {
          var next = root.classList.contains("layout-chat")
            ? "original" : "chat";
          root.classList.remove("layout-chat", "layout-original");
          root.classList.add("layout-" + next);
          layoutBtn.innerHTML = next === "chat" ? ICONS.wide : ICONS.chat;
          store("layout", next);
        });
      bar.appendChild(layoutBtn);
    }
    if (bar.children.length) document.body.appendChild(bar);

    // call status icons: green = connected, red = cancelled/missed
    if (f.bubbles) {
      var PHONE = '<svg viewBox="0 0 24 24"><path d="M6.6 10.8c1.5 2.8 3.8 5.1 6.6 6.6l2.2-2.2c.3-.3.7-.4 1-.2 1.1.4 2.4.6 3.6.6.6 0 1 .4 1 1V20c0 .6-.4 1-1 1C10.6 21 3 13.4 3 4c0-.6.4-1 1-1h3.5c.6 0 1 .4 1 1 0 1.3.2 2.5.6 3.6.1.3 0 .7-.2 1l-2.3 2.2z"/></svg>';
      document.querySelectorAll(".media_call").forEach(function (call) {
        if (call.querySelector(".tg_call_icon")) return;
        var status = call.querySelector(".status");
        var bad = status &&
          /cancel|missed|declined|busy|no answer/i.test(status.textContent);
        var icon = document.createElement("span");
        icon.className = "tg_call_icon " + (bad ? "bad" : "ok");
        icon.innerHTML = PHONE;
        call.insertBefore(icon, call.firstChild);
      });
    }

    if (f.media) {
      document.addEventListener("click", function (e) {
        var a = e.target.closest && e.target.closest("a.photo_wrap");
        if (!a) return;
        e.preventDefault();
        var box = document.createElement("div");
        box.className = "tg_lightbox";
        var img = document.createElement("img");
        img.src = a.getAttribute("href");
        box.appendChild(img);
        box.onclick = function () { box.remove(); };
        document.addEventListener("keydown", function esc(ev) {
          if (ev.key === "Escape") {
            box.remove();
            document.removeEventListener("keydown", esc);
          }
        });
        document.body.appendChild(box);
      });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
"""


def main():
    ap = argparse.ArgumentParser(
        description="Mejora la visualización de un export HTML de Telegram. "
                    "Cada mejora es opcional y reversible.")
    ap.add_argument("export", help="carpeta del export (contiene messages*.html)")
    ap.add_argument("--me", default=None,
                    help="tu nombre tal y como aparece en el chat (necesario "
                         "para el modo chat)")
    ap.add_argument("--layout", default="both",
                    choices=["both", "chat", "original"],
                    help="disposición: chat (tus mensajes a la derecha), "
                         "original (ancho completo) o both (conmutable)")
    for feat, desc in (("bubbles", "burbujas y fondo de chat"),
                       ("quotes", "citas de respuesta"),
                       ("theme", "conmutador claro/oscuro"),
                       ("media", "vídeo/audio en línea y visor de fotos"),
                       ("note", "nota final con instrucciones")):
        ap.add_argument(f"--no-{feat}", dest=feat, action="store_false",
                        help=f"desactivar: {desc}")
    ap.add_argument("--no-fullwidth", dest="fullwidth", action="store_false",
                    help="mantener la columna centrada en lugar de ocupar "
                         "toda la pantalla")
    ap.add_argument("--restore", action="store_true",
                    help="desmejorar: deshace todas las mejoras y restaura "
                         "el diseño original del export")
    args = ap.parse_args()

    export_dir = Path(args.export).resolve()
    if not export_dir.is_dir():
        sys.exit(f"error: {export_dir} no es una carpeta")
    try:
        if args.restore:
            restore(export_dir)
        else:
            enhance(export_dir, args.me, args.layout,
                    {k: getattr(args, k) for k in ALL_FEATURES},
                    fullwidth=args.fullwidth)
    except ValueError as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
