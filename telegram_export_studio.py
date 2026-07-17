#!/usr/bin/env python3
"""Telegram Export Studio — GUI local para fusionar, compactar y mejorar
exportaciones HTML de Telegram.

Interfaz web local (Material Design) sobre telegram_export_fuser.py,
telegram_export_compactor.py y telegram_export_enhancer.py. Sin
dependencias externas: solo la biblioteca estándar de Python.

Uso:
    python telegram_export_studio.py

Se abre automáticamente en el navegador (http://localhost:<puerto>).
Todo se ejecuta en local; no se envía nada a ningún servidor.
"""

import contextlib
import hashlib
import io
import json
import os
import re
import socket
import string
import threading
import time
import webbrowser
from collections import Counter, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import telegram_export_fuser as tef
from telegram_export_fuser import (
    DATE_TITLE_RE, FROM_NAME_RE, fuse, order_exports, parse_size,
)
from telegram_export_compactor import compact
from telegram_export_enhancer import enhance, restore
from telegram_export_converter import (
    detect_formats, downgrade_json, enrich_json, from_json, load_chat, to_json,
)

# ---------------------------------------------------------------------------
# Backend helpers
# ---------------------------------------------------------------------------

PICK_LOCK = threading.Lock()
LAST_DIR = {"path": str(Path.cwd())}
INSPECTED_PARENTS = set()  # parents of inspected exports: locate hints

SKIP_DIRS = {
    "windows", "program files", "program files (x86)", "programdata",
    "$recycle.bin", "system volume information", "appdata", "node_modules",
    "__pycache__", ".git", "recovery", "perflogs",
}

JOB = {"state": "idle", "buf": None, "result": None, "error": None,
       "stage": None, "warnings": []}
JOB_LOCK = threading.Lock()

# Modo verbose: solo afecta a esta app de escritorio (y a los AIO, que la
# incluyen tal cual) — imprime timing por etapa en el log del trabajo, que
# ya se captura y se muestra en el <details> "job-log" del frontend. La
# versión web (GitHub Pages) no tiene este backend, así que no aplica ahí.
VERBOSE = {"on": False}
_stage_timing = {"key": None, "t0": 0.0, "last_log": 0.0}


def _progress(kind, payload):
    with JOB_LOCK:
        if kind == "stage":
            JOB["stage"] = payload
        else:
            JOB["warnings"].append(payload)
    if VERBOSE["on"] and kind == "stage":
        _log_verbose_stage(payload)


def _log_verbose_stage(payload):
    now = time.perf_counter()
    key = payload.get("key")
    frac = payload.get("frac")
    if key != _stage_timing["key"]:
        if _stage_timing["key"] is not None:
            elapsed = now - _stage_timing["t0"]
            print(f"[verbose] etapa '{_stage_timing['key']}' terminada "
                  f"en {elapsed:.2f}s")
        _stage_timing.update(key=key, t0=now, last_log=now)
        print(f"[verbose] etapa '{key}' iniciada"
              + (f" ({payload['name']})" if payload.get("name") else ""))
        return
    if frac is not None and now - _stage_timing["last_log"] >= 0.5:
        elapsed = now - _stage_timing["t0"]
        rate = frac / elapsed if elapsed > 0 else 0
        eta = (1 - frac) / rate if rate > 0 else float("inf")
        print(f"[verbose] {key}: {frac * 100:.0f}% — {elapsed:.1f}s "
              f"transcurridos, ETA ~{eta:.1f}s")
        _stage_timing["last_log"] = now


tef.progress_hook = _progress


def pick_folder(title: str):
    """Native folder picker (tkinter, stdlib). Single selection."""
    with PICK_LOCK:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askdirectory(
            title=title, parent=root, initialdir=LAST_DIR["path"])
        root.destroy()
        if path:
            LAST_DIR["path"] = path
        return path or None


def _pick_folders_win(title: str):
    """Multi-select folder picker via the Windows IFileOpenDialog COM API
    (FOS_PICKFOLDERS | FOS_ALLOWMULTISELECT). tkinter's folder dialog is
    single-selection only, so we talk to the shell directly with ctypes."""
    import ctypes
    from ctypes import POINTER, byref, c_ulong, c_ushort, c_ubyte, \
        c_void_p, c_wchar_p

    ole32 = ctypes.oledll.ole32

    class GUID(ctypes.Structure):
        _fields_ = [("d1", c_ulong), ("d2", c_ushort),
                    ("d3", c_ushort), ("d4", c_ubyte * 8)]

    def guid(text):
        g = GUID()
        ole32.CLSIDFromString(text, byref(g))
        return g

    def com_call(obj, index, *args, argtypes=()):
        vtbl = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
        fn = ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, *argtypes)(
            vtbl[index])
        return fn(obj, *args)

    def release(obj):
        if obj:
            com_call(obj, 2)  # IUnknown::Release

    CLSID_FileOpenDialog = guid("{DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7}")
    IID_IFileOpenDialog = guid("{D57C7288-D4AD-4768-BE02-9D969532D960}")
    IID_IShellItem = guid("{43826D1E-E718-42EE-BC55-A1E261C37BFE}")
    FOS_PICKFOLDERS, FOS_FORCEFS, FOS_MULTI = 0x20, 0x40, 0x200
    SIGDN_FILESYSPATH = 0x80058000
    ERROR_CANCELLED = -2147023673  # 0x800704C7

    try:
        ole32.CoInitializeEx(None, 0x2)  # apartment-threaded
    except OSError:
        pass  # already initialized in another mode

    dialog = c_void_p()
    ole32.CoCreateInstance(byref(CLSID_FileOpenDialog), None, 1,
                           byref(IID_IFileOpenDialog), byref(dialog))
    paths = []
    try:
        opts = c_ulong()
        com_call(dialog, 10, byref(opts),          # GetOptions
                 argtypes=(POINTER(c_ulong),))
        com_call(dialog, 9,                        # SetOptions
                 opts.value | FOS_PICKFOLDERS | FOS_FORCEFS | FOS_MULTI,
                 argtypes=(c_ulong,))
        com_call(dialog, 17, title, argtypes=(c_wchar_p,))  # SetTitle
        try:                                       # initial folder
            shell32 = ctypes.oledll.shell32
            start = c_void_p()
            shell32.SHCreateItemFromParsingName(
                LAST_DIR["path"], None, byref(IID_IShellItem), byref(start))
            com_call(dialog, 12, start, argtypes=(c_void_p,))  # SetFolder
            release(start)
        except OSError:
            pass
        try:
            com_call(dialog, 3, None, argtypes=(c_void_p,))  # Show
        except OSError as err:
            if err.winerror == ERROR_CANCELLED:
                return []
            raise
        items = c_void_p()
        com_call(dialog, 27, byref(items),         # GetResults
                 argtypes=(POINTER(c_void_p),))
        try:
            count = c_ulong()
            com_call(items, 7, byref(count),       # GetCount
                     argtypes=(POINTER(c_ulong),))
            for i in range(count.value):
                item = c_void_p()
                com_call(items, 8, i, byref(item),  # GetItemAt
                         argtypes=(c_ulong, POINTER(c_void_p)))
                name = c_wchar_p()
                com_call(item, 5, SIGDN_FILESYSPATH,  # GetDisplayName
                         byref(name),
                         argtypes=(c_ulong, POINTER(c_wchar_p)))
                if name.value:
                    paths.append(name.value)
                ole32.CoTaskMemFree(ctypes.cast(name, c_void_p))
                release(item)
        finally:
            release(items)
    finally:
        release(dialog)
    if paths:
        LAST_DIR["path"] = str(Path(paths[0]).parent)
    return paths


def pick_folders(title: str):
    """Multi-select folder picker with graceful fallback: Windows COM
    dialog when available, otherwise the single tkinter dialog."""
    if os.name == "nt":
        with PICK_LOCK:
            try:
                return _pick_folders_win(title)
            except OSError:
                pass
    single = pick_folder(title)
    return [single] if single else []


def _locate_match(cand: Path, size: int, digest: str,
                  fname: str = "messages.html") -> bool:
    mh = cand / fname
    try:
        if not mh.is_file() or mh.stat().st_size != size:
            return False
        with mh.open("rb") as fh:
            return hashlib.sha256(fh.read(4096)).hexdigest() == digest
    except OSError:
        return False


def locate_export(name: str, size: int, digest: str,
                  fname: str = "messages.html"):
    """Find the absolute path of a drag&dropped export folder.

    Browsers never reveal filesystem paths of dropped folders, so the
    client sends the folder NAME plus a fingerprint of one known file
    (messages.html, or result.json for JSON exports: byte size + sha256
    of the first 4 KiB) and we search the disk for a matching directory:
    recent locations first, then common user folders, then a shallow,
    time-budgeted sweep of the drives.
    """
    if (not name or "/" in name or "\\" in name or size <= 0
            or fname not in ("messages.html", "result.json")):
        return None
    roots = []

    def add(p):
        try:
            p = Path(p)
            if p.is_dir():
                r = p.resolve()
                if r not in roots:
                    roots.append(r)
        except OSError:
            pass

    add(LAST_DIR["path"])
    add(Path(LAST_DIR["path"]).parent)
    for p in sorted(INSPECTED_PARENTS):
        add(p)
    add(Path.cwd())
    home = Path.home()
    for sub in ("Downloads", "Downloads/Telegram Desktop", "Desktop",
                "Documents", "Descargas", "Escritorio", "Documentos"):
        add(home / sub)
    add(home)
    # non-system drives first: user data (and Telegram exports) usually
    # live there, and the system drive tree is by far the slowest to walk
    sysdrive = os.environ.get("SystemDrive", "C:")[0].upper()
    for drive in string.ascii_uppercase:
        if drive != sysdrive:
            add(f"{drive}:\\")
    add(f"{sysdrive}:\\")

    deadline = time.monotonic() + 8.0
    seen = set()
    queue = deque((r, 0) for r in roots)
    while queue and time.monotonic() < deadline:
        base, depth = queue.popleft()
        key = str(base).lower()
        if key in seen:
            continue
        seen.add(key)
        cand = base / name
        if cand.is_dir() and _locate_match(cand, size, digest, fname):
            LAST_DIR["path"] = str(base)
            return str(cand)
        if depth >= 3:
            continue
        try:
            with os.scandir(base) as it:
                for entry in it:
                    if (entry.is_dir(follow_symlinks=False)
                            and not entry.name.startswith(".")
                            and entry.name.lower() not in SKIP_DIRS):
                        # exact-name child: verify right away instead of
                        # waiting for its turn in the BFS queue
                        if (entry.name == name and _locate_match(
                                Path(entry.path), size, digest, fname)):
                            LAST_DIR["path"] = str(base)
                            return entry.path
                        queue.append((Path(entry.path), depth + 1))
        except OSError:
            continue
    return None


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def inspect_export(path: str) -> dict:
    d = Path(path).resolve()
    if not d.is_dir():
        raise ValueError(f"No es una carpeta: {d}")
    pages = sorted(d.glob("messages*.html"),
                   key=lambda p: int(re.search(r"(\d*)\.html$", p.name)
                                     .group(1) or 0))
    if not pages:
        raise ValueError(
            "La carpeta no contiene messages.html — no parece un export "
            "HTML de Telegram")

    ids = set()
    title = None
    first = last = None
    page_infos = []
    senders = Counter()
    for p in pages:
        html = p.read_text(encoding="utf-8")
        ids.update(int(m) for m in re.findall(r'id="message(\d+)"', html))
        senders.update(s.strip() for s in FROM_NAME_RE.findall(html))
        if title is None:
            tm = re.search(r'<div class="text bold">\s*\n(.*?)\n', html)
            title = tm.group(1).strip() if tm else None
        dates = DATE_TITLE_RE.findall(html)
        if dates:
            if first is None:
                dd, mo, yy, *_ = dates[0]
                first = f"{dd}/{mo}/{yy}"
            dd, mo, yy, *_ = dates[-1]
            last = f"{dd}/{mo}/{yy}"
        page_infos.append({"name": p.name,
                           "size": human_size(p.stat().st_size)})

    media_bytes = sum(
        f.stat().st_size for f in d.rglob("*")
        if f.is_file() and f.suffix != ".html")
    INSPECTED_PARENTS.add(str(d.parent))
    return {
        "path": str(d),
        "name": d.name,
        "title": title,
        "messages": len(ids),
        "pages": page_infos,
        "first": first,
        "last": last,
        "media_size": human_size(media_bytes),
        "senders": [{"name": n, "count": c}
                    for n, c in senders.most_common()],
        "kind": "group" if len(senders) > 2 else "private",
        "enhanced": 'css/enhanced.css' in
                    pages[0].read_text(encoding="utf-8")[:4000],
    }


def start_job(fn):
    with JOB_LOCK:
        if JOB["state"] == "running":
            raise RuntimeError("Ya hay una operación en curso")
        buf = io.StringIO()
        JOB.update(state="running", buf=buf, result=None, error=None,
                   stage=None, warnings=[])

    def target():
        try:
            with contextlib.redirect_stdout(buf), \
                    contextlib.redirect_stderr(buf):
                result = fn()
            with JOB_LOCK:
                JOB.update(state="done", result=result)
        except (Exception, SystemExit) as e:
            with JOB_LOCK:
                JOB.update(state="error",
                           error=str(e) or e.__class__.__name__)

    threading.Thread(target=target, daemon=True).start()


def job_status() -> dict:
    with JOB_LOCK:
        return {
            "state": JOB["state"],
            "log": JOB["buf"].getvalue() if JOB["buf"] else "",
            "result": JOB["result"],
            "error": JOB["error"],
            "stage": JOB["stage"],
            "warnings": list(JOB["warnings"]),
        }


def do_fuse(exports, output, page_size, force=False):
    dirs = [Path(p).resolve() for p in exports]
    if len(dirs) < 2:
        raise ValueError("Añade al menos dos exports para fusionar")
    out = Path(output).resolve()
    if out in dirs:
        raise ValueError("La carpeta de destino no puede ser uno de los "
                         "exports de origen")
    return fuse(order_exports(dirs), out, parse_size(page_size),
                force=force)


def do_compact(export, mode, value):
    d = Path(export).resolve()
    if mode == "files":
        n = int(value)
        if n < 1:
            raise ValueError("El número de archivos debe ser al menos 1")
        return compact(d, n, None)
    return compact(d, None, parse_size(str(value)))


def do_enhance(export, me, layout, features=None, fullwidth=True):
    return enhance(Path(export).resolve(), me or None, layout, features,
                   fullwidth=fullwidth)


def inspect_convert(path: str) -> dict:
    """Inspect a folder for the converter: HTML export, JSON export,
    enriched-only JSON, or several at once. Unlike inspect_export, a
    folder without messages.html is fine as long as it holds a
    result.json or a result_enriched.json."""
    d = Path(path).resolve()
    if not d.is_dir():
        raise ValueError(f"No es una carpeta: {d}")
    found = detect_formats(d)
    if not found["html"] and not found["json"] and not found["enriched"]:
        raise ValueError(
            "La carpeta no contiene messages.html, result.json ni "
            "result_enriched.json — no parece un export de Telegram")

    info = {"path": str(d), "name": d.name, "title": None, "messages": 0,
            "has_html": found["html"], "has_json": found["json"],
            "has_enriched": found["enriched"]}
    if found["html"]:
        base = inspect_export(path)
        info.update(title=base["title"], messages=base["messages"])
    elif found["json"]:
        doc = load_chat(d / "result.json")  # raises on full-account export
        info.update(title=doc.get("name"),
                    messages=len(doc.get("messages", [])))
    else:
        doc = load_chat(d / "result_enriched.json")
        info.update(title=doc.get("name"),
                    messages=len(doc.get("messages", [])))
    INSPECTED_PARENTS.add(str(d.parent))
    return info


def do_convert(export, mode, faithful=False):
    d = Path(export).resolve()
    if mode == "tojson":
        return to_json(d, faithful=faithful)
    if mode == "tohtml":
        return from_json(d)
    if mode == "enrich":
        return enrich_json(d)
    if mode == "downgrade":
        return downgrade_json(d)
    raise ValueError(f"Modo de conversión desconocido: {mode}")


# ---------------------------------------------------------------------------
# Frontend (single page, Material Design 3 inspired)
# ---------------------------------------------------------------------------

PAGE = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Telegram Export Studio</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCA2NCA2NCI+CjxkZWZzPjxsaW5lYXJHcmFkaWVudCBpZD0iZyIgeDE9IjAiIHkxPSIwIiB4Mj0iMSIgeTI9IjEiPgo8c3RvcCBvZmZzZXQ9IjAiIHN0b3AtY29sb3I9IiMyYWFiZWUiLz48c3RvcCBvZmZzZXQ9IjEiIHN0b3AtY29sb3I9IiMxYThmZDEiLz4KPC9saW5lYXJHcmFkaWVudD48L2RlZnM+CjxjaXJjbGUgY3g9IjMyIiBjeT0iMzIiIHI9IjMyIiBmaWxsPSJ1cmwoI2cpIi8+CjxwYXRoIGZpbGw9IiNmZmZmZmYiIGQ9Ik00Ny44IDE3LjQgMTMuNiAzMC45Yy0xLjcuNy0xLjcgMS43LS4zIDIuMWw4LjggMi43IDMuNCAxMC4zYy40IDEuMiAxLjEgMS41IDIuMS45bDUuMS0zLjcgOC41IDYuMmMxLjYgMS4xIDIuNi41IDMtMS40bDUuNi0yNi41Yy41LTIuMy0uOC0zLjItMi45LTIuNnoiLz4KPHBhdGggZmlsbD0iI2M5ZThmYiIgZD0iTTI2LjQgMzcuNCA0My44IDIyLjdjLjktLjcgMS43LS4zIDEgLjZMMjguMSAzOS42bC0uNiA3LjMtMy4xLTkuNXoiLz4KPC9zdmc+">
<style>
:root {
  --bg: #f6f8fb;
  --surface: #ffffff;
  --surface-2: #eef3f9;
  --outline: #e2e8f0;
  --on: #1a1f24;
  --muted: #62707d;
  --primary: #2296d4;
  --primary-strong: #1181bd;
  --on-primary: #ffffff;
  --primary-soft: #e5f3fb;
  --ok: #1e8e3e;
  --err: #d93025;
  --warn: #b26a00;
  --warn-soft: #fdf3e3;
  --shadow: 0 1px 2px rgba(20,40,60,.06), 0 4px 16px rgba(20,40,60,.07);
  --radius: 20px;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0f1418;
    --surface: #1a2128;
    --surface-2: #232c35;
    --outline: #2d3944;
    --on: #e8edf2;
    --muted: #94a3b1;
    --primary: #4db8ec;
    --primary-strong: #6ec6f1;
    --on-primary: #06222f;
    --primary-soft: #173341;
    --ok: #6dd58c;
    --err: #f28b82;
    --warn: #f5b954;
    --warn-soft: #33290f;
    --shadow: 0 1px 2px rgba(0,0,0,.35), 0 6px 20px rgba(0,0,0,.3);
  }
}
* { box-sizing: border-box; margin: 0; }
html { color-scheme: light dark; }
body {
  background: var(--bg); color: var(--on);
  font: 15px/1.5 "Segoe UI Variable Text", "Segoe UI", -apple-system,
        "SF Pro Text", Roboto, system-ui, sans-serif;
  -webkit-font-smoothing: antialiased;
  padding: 32px 20px 80px;
}
.wrap { max-width: 720px; margin: 0 auto; }

/* header */
header { display: flex; align-items: center; gap: 16px; margin-bottom: 28px; }
.logo {
  width: 52px; height: 52px; border-radius: 15px; flex: none;
  background: linear-gradient(135deg, #2aabee, #1683c9);
  display: grid; place-items: center;
  box-shadow: 0 6px 18px rgba(34,150,212,.35);
}
.logo svg { width: 27px; height: 27px; fill: #fff; transform: translateX(-2px); }
header h1 {
  font-size: 22px; font-weight: 650; letter-spacing: -.2px;
  font-family: "Segoe UI Variable Display", "Segoe UI", -apple-system,
               "SF Pro Display", Roboto, sans-serif;
}
header p { color: var(--muted); font-size: 13.5px; margin-top: 1px; }
header .grow { flex: 1; }
select.lang {
  appearance: none; font: inherit; font-size: 13px; font-weight: 600;
  color: var(--muted); background: var(--surface-2); border: 0;
  border-radius: 999px; padding: 8px 14px; cursor: pointer; outline: none;
}

/* segmented tabs */
.tabs {
  display: inline-flex; background: var(--surface-2); border-radius: 999px;
  padding: 4px; margin-bottom: 20px; position: relative;
}
.tabs button {
  appearance: none; border: 0; background: none; color: var(--muted);
  font: inherit; font-weight: 600; font-size: 14px; cursor: pointer;
  padding: 8px 20px; border-radius: 999px; position: relative; z-index: 1;
  transition: color .25s;
}
.tabs button.active { color: var(--on); }
.tabs .pill {
  position: absolute; top: 4px; bottom: 4px; border-radius: 999px;
  background: var(--surface); box-shadow: var(--shadow);
  transition: left .3s cubic-bezier(.4,0,.2,1), width .3s cubic-bezier(.4,0,.2,1);
}

/* cards */
.card {
  background: var(--surface); border-radius: var(--radius);
  box-shadow: var(--shadow); padding: 22px 24px; margin-bottom: 16px;
  animation: rise .35s cubic-bezier(.2,.7,.3,1) both;
}
@keyframes rise { from { opacity: 0; transform: translateY(10px); } }
.card h2 {
  font-size: 12.5px; font-weight: 650; letter-spacing: .8px;
  text-transform: uppercase; color: var(--muted); margin-bottom: 16px;
}

/* export list */
.export-item {
  display: flex; align-items: center; gap: 14px;
  padding: 12px 14px; border: 1px solid var(--outline);
  border-radius: 14px; margin-bottom: 10px;
  animation: rise .3s cubic-bezier(.2,.7,.3,1) both;
}
.export-item .ficon {
  width: 40px; height: 40px; border-radius: 11px; flex: none;
  background: var(--primary-soft); display: grid; place-items: center;
}
.export-item .ficon svg { width: 20px; height: 20px; fill: var(--primary); }
.export-item .info { flex: 1; min-width: 0; }
.export-item .info b { font-size: 14.5px; font-weight: 600; display: block;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.export-item .info span { font-size: 12.5px; color: var(--muted); display: block;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.iconbtn {
  appearance: none; border: 0; background: none; cursor: pointer;
  width: 36px; height: 36px; border-radius: 50%; flex: none;
  display: grid; place-items: center; transition: background .2s;
}
.iconbtn svg { width: 19px; height: 19px; fill: var(--muted); }
.iconbtn:hover { background: var(--surface-2); }
.iconbtn:hover svg { fill: var(--err); }
#shutdown-btn.closed { pointer-events: none; }
#shutdown-btn.closed svg { fill: var(--ok); }
#verbose-btn:hover svg { fill: var(--primary); }
#verbose-btn.active { background: var(--primary-soft); }
#verbose-btn.active svg { fill: var(--primary); }

.empty {
  border: 1.5px dashed var(--outline); border-radius: 14px;
  padding: 28px; text-align: center; color: var(--muted);
  font-size: 13.5px; margin-bottom: 10px; transition: all .2s;
}
.empty svg { width: 30px; height: 30px; fill: var(--muted); opacity: .55;
  display: block; margin: 0 auto 8px; }
.empty:hover { border-color: var(--primary); color: var(--primary-strong); }
.empty.mini { padding: 14px; }
.empty.mini svg { width: 20px; height: 20px; margin-bottom: 4px; }

/* buttons */
.btn {
  appearance: none; border: 0; cursor: pointer; font: inherit;
  font-weight: 600; border-radius: 999px; transition: all .2s;
  display: inline-flex; align-items: center; gap: 8px; justify-content: center;
}
.btn:active { transform: scale(.97); }
.btn.tonal {
  background: var(--primary-soft); color: var(--primary-strong);
  padding: 10px 20px; font-size: 14px;
}
.btn.tonal:hover { filter: brightness(.96); }
.btn.tonal svg { width: 18px; height: 18px; fill: currentColor; }
.btn.tonal.danger {
  background: var(--err); color: #fff;
}
.btn.tonal.danger:hover { filter: brightness(1.08); }
.btn.filled {
  background: linear-gradient(135deg, #2aabee, #1a8fd1);
  color: #fff; padding: 15px 34px; font-size: 15.5px; width: 100%;
  box-shadow: 0 6px 20px rgba(34,150,212,.35);
}
.btn.filled:hover { box-shadow: 0 8px 26px rgba(34,150,212,.45); filter: brightness(1.04); }
.btn.filled:disabled {
  background: var(--surface-2); color: var(--muted);
  box-shadow: none; cursor: default; transform: none;
}
.btn.filled svg { width: 20px; height: 20px; fill: currentColor; }
.btn.text {
  background: none; color: var(--primary-strong);
  padding: 9px 16px; font-size: 14px;
}
.btn.text:hover { background: var(--primary-soft); }
.btn.text svg { width: 17px; height: 17px; fill: currentColor; }

/* inputs */
.field { display: flex; gap: 10px; align-items: center; }
.field input[type=text], .field input[type=number], .field select,
select.unit {
  flex: 1; font: inherit; color: var(--on);
  background: var(--surface-2); border: 1.5px solid transparent;
  border-radius: 12px; padding: 11px 14px; outline: none;
  transition: border-color .2s, background .2s; min-width: 0;
}
.field input:focus, .field select:focus {
  border-color: var(--primary); background: var(--surface); }
.browse {
  flex: none; width: 44px; height: 44px; border-radius: 12px;
  background: var(--primary-soft); border: 0; cursor: pointer;
  display: grid; place-items: center; transition: filter .2s;
}
.browse:hover { filter: brightness(.95); }
.browse svg { width: 20px; height: 20px; fill: var(--primary-strong); }

/* chips */
.chips { display: flex; flex-wrap: wrap; gap: 8px; }
.chip {
  appearance: none; cursor: pointer; font: inherit; font-size: 13.5px;
  font-weight: 600; color: var(--muted);
  background: var(--surface); border: 1.5px solid var(--outline);
  border-radius: 999px; padding: 8px 16px; transition: all .2s;
}
.chip:hover { background: var(--surface-2); }
.chip.sel {
  background: var(--primary-soft); border-color: var(--primary);
  color: var(--primary-strong);
}
.custom-size { display: none; margin-top: 12px; max-width: 260px; }
.custom-size.show { display: flex; }
select.unit { flex: none; width: 84px; cursor: pointer; }

.hint { font-size: 12.5px; color: var(--muted); margin-top: 12px; }
.label { font-size: 13.5px; font-weight: 600; margin: 18px 0 10px; }
.label:first-of-type { margin-top: 0; }

/* segmented mini */
.seg2 { display: inline-flex; background: var(--surface-2);
  border-radius: 12px; padding: 4px; gap: 2px; }
.seg2 button {
  appearance: none; border: 0; background: none; font: inherit;
  font-size: 13.5px; font-weight: 600; color: var(--muted);
  padding: 8px 16px; border-radius: 9px; cursor: pointer; transition: all .2s;
}
.seg2 button.active { background: var(--surface); color: var(--on);
  box-shadow: var(--shadow); }

/* pages summary */
.pagelist { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
.pagelist span {
  font-size: 12px; font-weight: 600; color: var(--muted);
  background: var(--surface-2); border-radius: 8px; padding: 4px 10px;
  font-family: Consolas, "SF Mono", monospace;
}

/* job */
#job { display: none; }
#job.show { display: block; }
.job-head { display: flex; align-items: center; gap: 12px; margin-bottom: 14px; }
.spinner {
  width: 22px; height: 22px; flex: none; border-radius: 50%;
  border: 3px solid var(--primary-soft); border-top-color: var(--primary);
  animation: spin .8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
.job-head b { flex: 1; font-size: 15px; font-weight: 600; }
.job-head .pct { font-size: 13.5px; font-weight: 700; color: var(--primary-strong);
  font-variant-numeric: tabular-nums; }
.progress { height: 6px; border-radius: 6px; overflow: hidden;
  background: var(--primary-soft); margin-bottom: 4px; }
.progress i { display: block; height: 100%; width: 0%;
  background: linear-gradient(90deg, #2aabee, #1a8fd1); border-radius: 6px;
  transition: width .35s cubic-bezier(.2,.7,.3,1); }
.progress.indet i { width: 40% !important;
  animation: slide 1.2s cubic-bezier(.4,0,.6,1) infinite; }
@keyframes slide {
  0% { transform: translateX(-120%); } 100% { transform: translateX(320%); } }

.warns { margin-top: 12px; display: flex; flex-direction: column; gap: 8px; }
.warn-item {
  display: flex; gap: 10px; align-items: flex-start;
  background: var(--warn-soft); border-radius: 12px; padding: 10px 14px;
  font-size: 13px; color: var(--warn);
  animation: rise .3s cubic-bezier(.2,.7,.3,1) both;
}
.warn-item svg { width: 17px; height: 17px; fill: var(--warn); flex: none;
  margin-top: 1px; }
.warn-item span { word-break: break-all; }
/* permanent destructive-mode banner (converter, faithful mode) */
.warn-item.danger {
  background: color-mix(in srgb, var(--err) 12%, transparent);
  color: var(--err); font-weight: 600;
  border: 1.5px solid color-mix(in srgb, var(--err) 45%, transparent);
}
.warn-item.danger svg { fill: var(--err); }
.warn-item.danger span { word-break: normal; }

details.logbox { margin-top: 14px; }
details.logbox summary {
  cursor: pointer; font-size: 13px; font-weight: 600; color: var(--muted);
  list-style: none; display: inline-flex; align-items: center; gap: 6px;
  padding: 6px 12px; border-radius: 999px; transition: background .2s;
}
details.logbox summary:hover { background: var(--surface-2); }
details.logbox summary::-webkit-details-marker { display: none; }
.log {
  font: 12.5px/1.7 Consolas, "SF Mono", "Cascadia Code", monospace;
  background: var(--surface-2); border-radius: 12px; padding: 14px 16px;
  white-space: pre-wrap; word-break: break-word; color: var(--muted);
  max-height: 200px; overflow-y: auto; margin-top: 8px;
}
.result { display: none; align-items: flex-start; gap: 14px; margin-top: 16px; }
.result.show { display: flex; }
.result .ricon { width: 40px; height: 40px; border-radius: 50%; flex: none;
  display: grid; place-items: center; }
.result .ricon.ok { background: color-mix(in srgb, var(--ok) 14%, transparent); }
.result .ricon.err { background: color-mix(in srgb, var(--err) 14%, transparent); }
.result .ricon svg { width: 22px; height: 22px; }
.result .ricon.ok svg { fill: var(--ok); }
.result .ricon.err svg { fill: var(--err); }
.result b { display: block; font-size: 15px; margin-bottom: 2px; }
.result p { font-size: 13.5px; color: var(--muted); word-break: break-all; }
.result .actions { display: flex; gap: 6px; margin-top: 10px; flex-wrap: wrap; }

/* drag & drop */
.card.dropping {
  outline: 2px dashed var(--primary); outline-offset: -8px;
  background: color-mix(in srgb, var(--primary) 6%, var(--surface));
}
.card.dropping .empty { border-color: var(--primary); color: var(--primary-strong); }

/* switches */
.switchrow { display: flex; align-items: center; gap: 14px;
  padding: 11px 0; border-bottom: 1px solid var(--outline); }
.switchrow:last-of-type { border-bottom: 0; }
.switchrow .sw-txt { flex: 1; min-width: 0; }
.switchrow .sw-txt b { font-size: 14px; font-weight: 600; display: block; }
.switchrow .sw-txt span { font-size: 12.5px; color: var(--muted); display: block; }
.switch { position: relative; width: 46px; height: 26px; flex: none; }
.switch input { position: absolute; inset: 0; width: 100%; height: 100%;
  opacity: 0; margin: 0; cursor: pointer; z-index: 1; }
.switch i {
  position: absolute; inset: 0; border-radius: 999px;
  background: var(--surface-2); border: 1.5px solid var(--outline);
  transition: all .2s;
}
.switch i::after {
  content: ""; position: absolute; top: 3px; left: 3px;
  width: 17px; height: 17px; border-radius: 50%;
  background: var(--muted); transition: all .2s;
}
.switch input:checked + i { background: var(--primary); border-color: var(--primary); }
.switch input:checked + i::after { left: 22px; background: #fff; }
.subopts { transition: opacity .25s; }
.subopts.off { opacity: .4; pointer-events: none; }

/* badge */
.badge { display: inline-block; font-size: 11px; font-weight: 700;
  border-radius: 6px; padding: 1px 7px; margin-left: 6px;
  background: var(--primary-soft); color: var(--primary-strong);
  vertical-align: 1px; }

/* modal */
#modal-back {
  position: fixed; inset: 0; z-index: 100;
  background: rgba(8, 14, 20, .48); display: none; place-items: center;
  animation: fadein .2s ease;
}
#modal-back.show { display: grid; }
@keyframes fadein { from { opacity: 0; } }
.modal {
  background: var(--surface); border-radius: 22px; padding: 26px 28px;
  max-width: 420px; margin: 20px; box-shadow: 0 18px 60px rgba(0,0,0,.35);
  animation: rise .25s cubic-bezier(.2,.7,.3,1) both;
}
.modal h3 { font-size: 16.5px; font-weight: 650; margin-bottom: 10px; }
.modal p { font-size: 14px; color: var(--muted); line-height: 1.55; }

#shutdown-screen {
  position: fixed; inset: 0; z-index: 200; display: none;
  place-items: center; text-align: center; background: var(--bg);
}
#shutdown-screen.show { display: grid; }
#shutdown-screen svg { width: 46px; height: 46px; fill: var(--ok); margin-bottom: 14px; }
#shutdown-screen h2 { font-size: 19px; margin-bottom: 6px; }
#shutdown-screen p { font-size: 14px; color: var(--muted); }
.modal .mact { display: flex; justify-content: flex-end; gap: 8px;
  margin-top: 22px; }
.btn.outline {
  background: none; border: 1.5px solid var(--outline); color: var(--on);
  padding: 9px 18px; font-size: 14px;
}
.btn.outline:hover { background: var(--surface-2); }

/* secondary action row */
.action-row { display: flex; gap: 10px; }
.action-row .btn.filled { flex: 1; }
.btn.danger-tonal {
  background: color-mix(in srgb, var(--err) 12%, transparent);
  color: var(--err); padding: 15px 22px; font-size: 14.5px;
}
.btn.danger-tonal:hover { filter: brightness(1.05); }
.btn.danger-tonal svg { width: 19px; height: 19px; fill: currentColor; }

/* snackbar */
#snack {
  position: fixed; left: 50%; bottom: 28px; transform: translate(-50%, 80px);
  background: var(--on); color: var(--bg); font-size: 14px; font-weight: 500;
  padding: 13px 22px; border-radius: 12px; box-shadow: var(--shadow);
  opacity: 0; transition: all .3s cubic-bezier(.2,.7,.3,1);
  max-width: 90vw; z-index: 50;
}
#snack.show { transform: translate(-50%, 0); opacity: 1; }

footer { text-align: center; color: var(--muted); font-size: 12px;
  margin-top: 36px; opacity: .8; }
.srclink { display: inline-flex; align-items: center; gap: 6px;
  margin-top: 14px; font-size: 12.5px; color: var(--muted);
  text-decoration: none; opacity: .75; transition: opacity .2s; }
.srclink:hover { opacity: 1; text-decoration: underline; color: var(--accent); }
.srclink svg { width: 14px; height: 14px; fill: currentColor; flex: none; }
</style>
</head>
<body>
<div class="wrap">

<header>
  <div class="logo">
    <svg viewBox="0 0 24 24"><path d="M21.4 3.1 2.2 10.5c-1 .4-1 1.8.1 2.1l4.9 1.5 1.9 5.9c.3.9 1.4 1.1 2 .4l2.7-2.8 4.9 3.6c.8.6 2 .2 2.2-.9l3-15.3c.2-1.2-.9-2.1-2-1.7l-.5-.2zM8.5 13.8l9.5-6-7.3 7.1-.3 3.2-1.9-4.3z"/></svg>
  </div>
  <div>
    <h1>Telegram Export Studio</h1>
    <p data-i18n="subtitle"></p>
  </div>
  <div class="grow"></div>
  <select class="lang" id="lang" title="Language">
    <option value="es">🌐 ES</option>
    <option value="en">🌐 EN</option>
    <option value="fr">🌐 FR</option>
    <option value="de">🌐 DE</option>
    <option value="pt">🌐 PT</option>
    <option value="it">🌐 IT</option>
    <option value="ru">🌐 RU</option>
    <option value="zh">🌐 中文</option>
    <option value="ja">🌐 日本語</option>
    <option value="hi">🌐 हिन्दी</option>
    <option value="ar">🌐 العربية</option>
  </select>
  <button class="iconbtn" id="verbose-btn" title="Detalles técnicos (log verbose)" onclick="toggleVerbose()">
    <svg viewBox="0 0 24 24"><path d="M20 8h-2.81a5.985 5.985 0 0 0-1.82-1.96L17 4.41 15.59 3l-2.17 2.17a6.002 6.002 0 0 0-2.83 0L8.41 3 7 4.41l1.62 1.63A5.985 5.985 0 0 0 6.81 8H4v2h2.09c-.05.33-.09.66-.09 1v1H4v2h2v1c0 .34.04.67.09 1H4v2h2.81c1.04 1.79 2.97 3 5.19 3s4.15-1.21 5.19-3H20v-2h-2.09c.05-.33.09-.66.09-1v-1h2v-2h-2v-1c0-.34-.04-.67-.09-1H20V8zm-6 8h-4v-2h4v2zm0-4h-4v-2h4v2z"/></svg>
  </button>
  <button class="iconbtn" id="shutdown-btn" title="" onclick="confirmShutdown()">
    <svg viewBox="0 0 24 24"><path d="M13 3h-2v10h2V3zm4.83 2.17-1.42 1.42A6.92 6.92 0 0 1 19 12a7 7 0 1 1-11.83-5.03L5.76 5.56A9 9 0 1 0 21 12a8.94 8.94 0 0 0-3.17-6.83z"/></svg>
  </button>
</header>

<div class="tabs" id="tabs">
  <div class="pill" id="pill"></div>
  <button data-view="fuse" class="active" data-i18n="tab_fuse"></button>
  <button data-view="compact" data-i18n="tab_compact"></button>
  <button data-view="enhance" data-i18n="tab_enhance"></button>
  <button data-view="convert" data-i18n="tab_convert"></button>
</div>

<!-- ================= FUSE ================= -->
<section id="view-fuse">
  <div class="card" id="fuse-card">
    <h2 data-i18n="fuse_sources"></h2>
    <div id="export-list"></div>
    <div class="empty" id="export-empty" style="cursor:pointer" onclick="addExport()">
      <svg viewBox="0 0 24 24"><path d="M10 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-8l-2-2z"/></svg>
      <span id="export-empty-text" data-i18n-html="fuse_empty"></span>
    </div>
  </div>

  <div class="card">
    <h2 data-i18n="dest_pagination"></h2>
    <div class="label" data-i18n="dest_label"></div>
    <div class="field">
      <input type="text" id="output" spellcheck="false">
      <button class="browse" onclick="browseOutput()">
        <svg viewBox="0 0 24 24"><path d="M20 6h-8l-2-2H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2zm0 12H4V8h16v10z"/></svg>
      </button>
    </div>
    <div class="label" data-i18n="size_label"></div>
    <div class="chips" id="fuse-chips">
      <button class="chip sel" data-size="500KB">500 KB</button>
      <button class="chip" data-size="1MB">1 MB</button>
      <button class="chip" data-size="5MB">5 MB</button>
      <button class="chip" data-size="0" data-i18n="chip_single"></button>
      <button class="chip" data-size="custom" data-i18n="chip_custom"></button>
    </div>
    <div class="field custom-size" id="fuse-custom">
      <input type="number" id="fuse-custom-n" value="2" min="1">
      <select class="unit" id="fuse-custom-u"><option>MB</option><option>KB</option></select>
    </div>
    <div class="hint" data-i18n="fuse_hint"></div>
  </div>

  <button class="btn filled" id="fuse-btn" onclick="runFuse()" disabled>
    <svg viewBox="0 0 24 24"><path d="M17 20.41 18.41 19 15 15.59 13.59 17 17 20.41zM7.5 8H11v5.59L5.59 19 7 20.41l6-6V8h3.5L12 3.5 7.5 8z"/></svg>
    <span id="fuse-btn-label"></span>
  </button>
</section>

<!-- ================= COMPACT ================= -->
<section id="view-compact" style="display:none">
  <div class="card" id="compact-card">
    <h2 data-i18n="compact_source"></h2>
    <div id="compact-sel" class="empty" style="cursor:pointer" onclick="pickCompact()">
      <svg viewBox="0 0 24 24"><path d="M10 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-8l-2-2z"/></svg>
      <span data-i18n-html="compact_pick"></span>
    </div>
    <div id="compact-info" style="display:none">
      <div class="export-item" style="margin-bottom:0">
        <div class="ficon"><svg viewBox="0 0 24 24"><path d="M10 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-8l-2-2z"/></svg></div>
        <div class="info"><b id="ci-name"></b><span id="ci-sub"></span></div>
        <button class="iconbtn" onclick="clearCompact()">
          <svg viewBox="0 0 24 24"><path d="M19 6.41 17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg>
        </button>
      </div>
      <div class="pagelist" id="ci-pages"></div>
    </div>
  </div>

  <div class="card">
    <h2 data-i18n="goal"></h2>
    <div class="seg2" id="compact-mode">
      <button data-mode="files" class="active" data-i18n="mode_files"></button>
      <button data-mode="size" data-i18n="mode_size"></button>
    </div>
    <div id="compact-files" style="margin-top:16px">
      <div class="field" style="max-width:200px">
        <input type="number" id="files-n" value="1" min="1">
      </div>
    </div>
    <div class="hint" style="margin-top:12px" id="goal-hint"></div>
    <div id="compact-size" style="display:none;margin-top:16px">
      <div class="chips" id="compact-chips">
        <button class="chip sel" data-size="1MB">1 MB</button>
        <button class="chip" data-size="5MB">5 MB</button>
        <button class="chip" data-size="10MB">10 MB</button>
        <button class="chip" data-size="custom" data-i18n="chip_custom"></button>
      </div>
      <div class="field custom-size" id="compact-custom">
        <input type="number" id="compact-custom-n" value="2" min="1">
        <select class="unit" id="compact-custom-u"><option>MB</option><option>KB</option></select>
      </div>
    </div>
    <div class="hint" style="margin-top:14px" data-i18n="inplace_hint"></div>
  </div>

  <button class="btn filled" id="compact-btn" onclick="runCompact()" disabled>
    <svg viewBox="0 0 24 24"><path d="M7.41 18.59 8.83 20 12 16.83 15.17 20l1.41-1.41L12 14l-4.59 4.59zm9.18-13.18L15.17 4 12 7.17 8.83 4 7.41 5.41 12 10l4.59-4.59z"/></svg>
    <span data-i18n="compact_btn"></span>
  </button>
</section>

<!-- ================= ENHANCE ================= -->
<section id="view-enhance" style="display:none">
  <div class="card" id="enhance-card">
    <h2 data-i18n="enhance_source"></h2>
    <div id="enhance-sel" class="empty" style="cursor:pointer" onclick="pickEnhance()">
      <svg viewBox="0 0 24 24"><path d="M10 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-8l-2-2z"/></svg>
      <span data-i18n-html="enhance_pick"></span>
    </div>
    <div id="enhance-info" style="display:none">
      <div class="export-item" style="margin-bottom:0">
        <div class="ficon"><svg viewBox="0 0 24 24"><path d="M12 2 9.2 8.6 2 9.3l5.5 4.7L5.8 21 12 17.3 18.2 21l-1.7-7 5.5-4.7-7.2-.7z"/></svg></div>
        <div class="info"><b id="ei-name"></b><span id="ei-sub"></span></div>
        <button class="iconbtn" onclick="clearEnhance()">
          <svg viewBox="0 0 24 24"><path d="M19 6.41 17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg>
        </button>
      </div>
    </div>
  </div>

  <div class="card">
    <h2 data-i18n="enhance_opts"></h2>

    <div class="switchrow">
      <div class="sw-txt"><b data-i18n="feat_bubbles"></b><span data-i18n="feat_bubbles_d"></span></div>
      <label class="switch"><input type="checkbox" id="opt-bubbles" checked><i></i></label>
    </div>
    <div class="subopts" id="bubbles-sub" style="padding:4px 0 10px">
      <div class="label" data-i18n="me_label"></div>
      <div class="field">
        <select id="me-select"><option value=""></option></select>
      </div>
      <div class="label" data-i18n="layout_label"></div>
      <div class="chips" id="layout-chips">
        <button class="chip sel" data-layout="both" data-i18n="layout_both"></button>
        <button class="chip" data-layout="chat" data-i18n="layout_chat"></button>
        <button class="chip" data-layout="original" data-i18n="layout_original"></button>
      </div>
      <div class="hint" id="layout-hint"></div>
    </div>
    <div class="switchrow">
      <div class="sw-txt"><b data-i18n="feat_fullwidth"></b><span data-i18n="feat_fullwidth_d"></span></div>
      <label class="switch"><input type="checkbox" id="opt-fullwidth" checked><i></i></label>
    </div>
    <div class="switchrow">
      <div class="sw-txt"><b data-i18n="feat_quotes"></b><span data-i18n="feat_quotes_d"></span></div>
      <label class="switch"><input type="checkbox" id="opt-quotes" checked><i></i></label>
    </div>
    <div class="switchrow">
      <div class="sw-txt"><b data-i18n="feat_theme"></b><span data-i18n="feat_theme_d"></span></div>
      <label class="switch"><input type="checkbox" id="opt-theme" checked><i></i></label>
    </div>
    <div class="switchrow">
      <div class="sw-txt"><b data-i18n="feat_media"></b><span data-i18n="feat_media_d"></span></div>
      <label class="switch"><input type="checkbox" id="opt-media" checked><i></i></label>
    </div>
    <div class="switchrow">
      <div class="sw-txt"><b data-i18n="feat_note"></b><span data-i18n="feat_note_d"></span></div>
      <label class="switch"><input type="checkbox" id="opt-note" checked><i></i></label>
    </div>
    <div class="hint" data-i18n="enhance_hint"></div>
  </div>

  <div class="action-row">
    <button class="btn filled" id="enhance-btn" onclick="runEnhance()" disabled>
      <svg viewBox="0 0 24 24"><path d="M12 2 9.2 8.6 2 9.3l5.5 4.7L5.8 21 12 17.3 18.2 21l-1.7-7 5.5-4.7-7.2-.7zM19 2l.9 2.1L22 5l-2.1.9L19 8l-.9-2.1L16 5l2.1-.9z"/></svg>
      <span data-i18n="enhance_btn"></span>
    </button>
    <button class="btn danger-tonal" id="restore-btn" onclick="runRestore()" style="display:none">
      <svg viewBox="0 0 24 24"><path d="M13 3a9 9 0 0 0-9 9H1l3.9 3.9L8.8 12H6a7 7 0 1 1 2.1 5l-1.4 1.4A9 9 0 1 0 13 3z"/></svg>
      <span data-i18n="restore_btn"></span>
    </button>
  </div>
</section>

<!-- ================= CONVERT ================= -->
<section id="view-convert" style="display:none">
  <div class="card" id="convert-card">
    <h2 data-i18n="convert_source"></h2>
    <div id="convert-sel" class="empty" style="cursor:pointer" onclick="pickConvert()">
      <svg viewBox="0 0 24 24"><path d="M10 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-8l-2-2z"/></svg>
      <span data-i18n-html="convert_pick"></span>
    </div>
    <div id="convert-info" style="display:none">
      <div class="export-item" style="margin-bottom:0">
        <div class="ficon"><svg viewBox="0 0 24 24"><path d="M6.99 11 3 15l3.99 4v-3H14v-2H6.99v-3zM21 9l-3.99-4v3H10v2h7.01v3L21 9z"/></svg></div>
        <div class="info"><b id="cv-name"></b><span id="cv-sub"></span></div>
        <button class="iconbtn" onclick="clearConvert()">
          <svg viewBox="0 0 24 24"><path d="M19 6.41 17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg>
        </button>
      </div>
      <div class="hint" id="cv-detected" style="font-weight:600"></div>
    </div>
  </div>

  <div class="card" id="convert-opts" style="display:none">
    <h2 data-i18n="convert_opts"></h2>
    <div id="cv-tojson" style="display:none">
      <div class="label" data-i18n="convert_mode"></div>
      <div class="seg2" id="cv-mode">
        <button data-mode="enriched" class="active" data-i18n="mode_enriched"></button>
        <button data-mode="faithful" data-i18n="mode_faithful"></button>
      </div>
      <div class="hint" id="cv-mode-hint" style="margin-top:12px"></div>
      <div class="warn-item danger" id="cv-faithful-warn" style="display:none;margin-top:14px">
        <svg viewBox="0 0 24 24"><path d="M1 21h22L12 2 1 21zm12-3h-2v-2h2v2zm0-4h-2v-4h2v4z"/></svg>
        <span data-i18n="faithful_warn"></span>
      </div>
      <div class="hint" data-i18n="tojson_hint" style="margin-top:12px"></div>
    </div>
    <div id="cv-tohtml" style="display:none">
      <div class="hint" data-i18n="tohtml_hint"></div>
    </div>
    <div id="cv-enrich" style="display:none">
      <div class="hint" data-i18n="enrich_hint"></div>
    </div>
    <div id="cv-eo" style="display:none">
      <div class="label" data-i18n="eo_choice"></div>
      <div class="seg2" id="cv-eo-mode">
        <button data-eo="tohtml" class="active" data-i18n="convert_btn_tohtml"></button>
        <button data-eo="downgrade" data-i18n="convert_btn_downgrade"></button>
      </div>
      <div class="hint" id="cv-eo-hint" style="margin-top:12px"></div>
      <div class="warn-item danger" id="cv-eo-warn" style="display:none;margin-top:14px">
        <svg viewBox="0 0 24 24"><path d="M1 21h22L12 2 1 21zm12-3h-2v-2h2v2zm0-4h-2v-4h2v4z"/></svg>
        <span data-i18n="downgrade_warn"></span>
      </div>
    </div>
  </div>

  <button class="btn filled" id="convert-btn" onclick="runConvert()" disabled>
    <svg viewBox="0 0 24 24"><path d="M6.99 11 3 15l3.99 4v-3H14v-2H6.99v-3zM21 9l-3.99-4v3H10v2h7.01v3L21 9z"/></svg>
    <span id="convert-btn-label"></span>
  </button>
</section>

<!-- ================= JOB ================= -->
<div class="card" id="job">
  <div class="job-head">
    <div class="spinner" id="job-spin"></div>
    <b id="job-stage"></b>
    <span class="pct" id="job-pct"></span>
  </div>
  <div class="progress indet" id="job-progress"><i id="job-bar"></i></div>
  <div class="warns" id="job-warns"></div>
  <details class="logbox">
    <summary data-i18n="job_log"></summary>
    <div class="log" id="job-log"></div>
  </details>
  <div class="result" id="job-result">
    <div class="ricon" id="job-ricon"></div>
    <div style="flex:1">
      <b id="job-rtitle"></b>
      <p id="job-rsub"></p>
      <div class="actions" id="job-actions"></div>
    </div>
  </div>
</div>

<footer>
  <div data-i18n="footer"></div>
  <a class="srclink" href="#" target="_blank" rel="noopener"><svg viewBox="0 0 16 16"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82a7.42 7.42 0 0 1 4 0c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z"/></svg><span data-i18n="srclink"></span></a>
</footer>
</div>

<div id="snack"></div>

<div id="shutdown-screen">
  <div>
    <svg viewBox="0 0 24 24"><path d="M9 16.2 4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4z"/></svg>
    <h2 data-i18n="shutdown_done_h"></h2>
    <p data-i18n="shutdown_done_p"></p>
  </div>
</div>

<div id="modal-back">
  <div class="modal">
    <h3 id="modal-title"></h3>
    <p id="modal-body"></p>
    <div class="mact">
      <button class="btn outline" id="modal-no"></button>
      <button class="btn tonal" id="modal-yes"></button>
    </div>
  </div>
</div>

<script>
"use strict";
const $ = id => document.getElementById(id);
const state = { exports: [], compact: null, enhance: null, view: "fuse",
                convert: null, convertAction: null };

/* URL del proyecto (código fuente) */
const REPO_URL = "https://github.com/Marcos-SA-git/Telegram-Export-Studio";
document.querySelectorAll(".srclink").forEach(a => { a.href = REPO_URL; });

/* =============== i18n =============== */
const I18N = {
es: {
  subtitle: "Fusiona, compacta y mejora exports de chats — 100% local",
  tab_fuse: "Fusionar", tab_compact: "Compactar", tab_enhance: "Mejorar",
  fuse_sources: "Exports de origen",
  fuse_empty: "Añade o arrastra aquí dos o más carpetas de export<br>de Telegram para fusionarlas en una sola",
  add_folder: "Añadir carpeta",
  dest_pagination: "Destino y paginación",
  dest_label: "Carpeta de destino",
  size_label: "Tamaño de página",
  chip_single: "Archivo único", chip_custom: "Personalizado",
  fuse_hint: "Telegram usa páginas de ~500 KB. Páginas más grandes = menos archivos messages.html.",
  fuse_btn: "Fusionar exports", fuse_btn_n: "Fusionar {n} exports",
  compact_source: "Export a compactar",
  compact_pick: "Elige o arrastra aquí la carpeta del export<br>(sirve un export de Telegram o el resultado de una fusión)",
  goal: "Objetivo", mode_files: "Número de archivos", mode_size: "Tamaño por página",
  files_hint: "1 = todo el historial en un único messages.html",
  goal_hint_files: "Eliges cuántos messages.html quieres al final (1 = todo el historial en uno) y el tamaño de cada página se calcula solo.",
  goal_hint_size: "Fijas el tamaño máximo de cada messages.html y la cantidad de archivos resulta del total del historial.",
  srclink: "Código abierto · ver en GitHub",
  inplace_hint: "Se repagina en el sitio: solo se reescriben los messages*.html; fotos, vídeos y audios no se tocan.",
  compact_btn: "Compactar",
  enhance_source: "Export a mejorar",
  enhance_pick: "Elige o arrastra aquí la carpeta del export<br>(original, fusionado o compactado)",
  enhance_opts: "Opciones de visualización",
  me_label: "¿Quién eres tú? (los mensajes de la persona elegida se resaltarán con otro color para indicar que son tuyos)",
  me_none: "— Solo diseño original, sin lado propio —",
  layout_label: "Disposición de los mensajes",
  layout_both: "Ambos (conmutable)", layout_chat: "Conversación", layout_original: "Original",
  layout_hint_original: "Todos los avatares a la izquierda, como en el export original de Telegram.",
  layout_hint_chat: "Avatares a ambos lados: el interlocutor a la izquierda y tú a la derecha.",
  layout_hint_both: "Un botón flotante en el chat permite alternar entre ambas disposiciones.",
  feat_fullwidth: "Ancho completo",
  feat_fullwidth_d: "Elimina la columna centrada de Telegram: el chat ocupa dinámicamente toda la pantalla",
  enhance_hint: "Burbujas estilo Telegram, citas de respuesta, tema claro/oscuro, vídeos y audios reproducibles y fotos en visor. Compatible: podrás seguir fusionando y compactando este export después.",
  enhance_btn: "Mejorar export",
  need_me: "Elige quién eres tú para el modo chat",
  already_enhanced: "Este export ya estaba mejorado; se actualizará",
  item_sub: "{msgs} mensajes · {pages} página(s) · media {media}",
  item_more: "+{n} más",
  job_fusing: "Fusionando exports…", job_compacting: "Compactando export…",
  job_enhancing: "Mejorando export…",
  job_done: "Completado", job_error: "Error",
  job_failed: "La operación ha fallado",
  job_log: "Ver registro completo",
  open_chat: "Abrir chat", open_folder: "Abrir carpeta",
  res_summary: "{msgs} mensajes en {pages} página(s)",
  res_range: "Del {a} al {b} · ",
  res_own: " · {n} mensajes tuyos",
  snack_dup: "Ese export ya está en la lista",
  snack_need_output: "Indica la carpeta de destino",
  stage_scan: "Escaneando {name}…", stage_merge: "Combinando y deduplicando mensajes…",
  stage_media: "Copiando media · {copied} archivos…",
  stage_write: "Escribiendo páginas…", stage_enhance: "Mejorando {name}…",
  stage_working: "Procesando…",
  pick_export: "Elige una carpeta de export de Telegram",
  pick_output: "Elige la carpeta de destino",
  pick_compact: "Elige el export a compactar",
  pick_enhance: "Elige el export a mejorar",
  feat_bubbles: "Burbujas y fondo de chat",
  feat_bubbles_d: "Apariencia de Telegram real, con disposición configurable",
  feat_quotes: "Citas de respuesta",
  feat_quotes_d: "Recuadro con autor y fragmento en vez de \"In reply to this message\"",
  feat_theme: "Modo claro / oscuro",
  feat_theme_d: "Botón flotante para cambiar de tema al ver el chat",
  feat_media: "Media en vivo",
  feat_media_d: "Vídeos y audios reproducibles en línea, fotos en visor",
  feat_note: "Mensaje final con instrucciones",
  feat_note_d: "Nota al final del chat con cómo usar estas herramientas",
  restore_btn: "Desmejorar",
  job_restoring: "Restaurando el diseño original…",
  stage_restore: "Restaurando {name}…",
  kind_group: "👥 Grupo · {n} participantes",
  kind_private: "👤 Chat privado",
  confirm_title: "¿Chats distintos?",
  confirm_mix: "«{a}» no coincide con «{b}». Los exports parecen de chats diferentes y fusionarlos mezclaría conversaciones. ¿Añadirlo igualmente?",
  confirm_kind: "Estás mezclando un chat grupal con uno privado. ¿Añadirlo igualmente?",
  btn_cancel: "Cancelar",
  btn_continue: "Añadir igualmente",
  fuse_add_more: "Añade o arrastra aquí otra carpeta de export",
  locating: "Localizando «{name}» en el disco…",
  locate_fail: "No se pudo localizar la carpeta en el disco. Añádela una vez con el botón y los siguientes arrastres desde esa ubicación funcionarán.",
  drop_only_folders: "Suelta carpetas de export, no archivos",
  drop_no_messages: "«{name}» no contiene messages.html",
  shutdown_tooltip: "Apagar la aplicación",
  shutdown_title: "¿Apagar la aplicación?",
  shutdown_body: "Se apagará y esta pestaña dejará de funcionar, <strong><u>incluso si hay algo en curso</u></strong>.",
  shutdown_confirm: "Apagar",
  shutdown_done_h: "Aplicación apagada",
  shutdown_done_p: "Ya puedes cerrar esta pestaña. Para volver a usar la app, abre de nuevo el script o el ejecutable.",
  tab_convert: "Conversor",
  convert_source: "Carpeta a convertir",
  convert_pick: "Elige o arrastra aquí la carpeta del export<br>(HTML, JSON o ambos — se detecta automáticamente)",
  convert_opts: "Opciones de conversión",
  convert_mode: "Modo de conversión a JSON",
  mode_enriched: "Enriquecida (recomendada)",
  mode_faithful: "Formato oficial",
  mode_hint_enriched: "JSON con el esquema oficial de Telegram más campos extra (texto de estado de llamadas, nombres de archivo…) para no perder nada de lo que contiene el HTML.",
  mode_hint_faithful: "Solo las claves del result.json oficial de Telegram Desktop, sin ningún campo extra.",
  faithful_warn: "PROCESO DESTRUCTIVO: el formato oficial descarta los datos que no contempla (dirección y texto de estado de las llamadas, nombres de archivo…) y BORRA las páginas messages*.html y los recursos web (css/, js/, images/) de la carpeta, para que quede igual que un export JSON real de Telegram. Si necesitas conservar esos datos o el HTML, usa el modo enriquecido.",
  tojson_hint: "El resultado se guarda como result.json dentro de la propia carpeta del export — así las rutas relativas de la media (photos/…, voice_messages/…) siguen funcionando. Si ya existe un result.json que no generó esta herramienta, la operación se detiene para no sobrescribir el export oficial.",
  tohtml_hint: "Se generará la vista HTML navegable (messages*.html) en la misma carpeta, incluyendo la estructura web (css/js/images) que el export JSON no trae. La media existente se referencia tal cual, sin copiarla. Las páginas messages*.html ya existentes nunca se sobrescriben sin confirmación.",
  enrich_hint: "Se combinarán ambos formatos: el result.json oficial (que contiene datos que el HTML no tiene: ids, fechas de edición, tamaños…) se enriquecerá con los campos extra recuperables del HTML. Se escribirá result_enriched.json sin tocar ninguno de los archivos originales.",
  eo_choice: "Solo hay un JSON enriquecido en esta carpeta. ¿Qué quieres hacer?",
  eo_hint_tohtml: "Se generarán las páginas messages*.html y los recursos web (css/js/images) a partir de este JSON enriquecido.",
  eo_hint_downgrade: "Se eliminarán los campos y la marca que añadió el enriquecido, dejando un result.json igual al formato oficial de Telegram.",
  downgrade_warn: "PROCESO DESTRUCTIVO: se perderán los datos extra que solo estaban en el JSON enriquecido (dirección y texto de estado de las llamadas, nombres de archivo…) y no podrán recuperarse sin el HTML original.",
  convert_btn_downgrade: "Bajar a formato oficial",
  res_removed_assets: " · páginas y recursos HTML eliminados",
  det_html: "Detectado: export HTML → se convertirá a JSON",
  det_json: "Detectado: export JSON → se generará la vista HTML",
  det_both: "Detectado: HTML + JSON a la vez → enriquecer el JSON",
  det_enriched_only: "Detectado: solo JSON enriquecido → elige qué hacer",
  both_title: "⚠ La carpeta contiene AMBOS formatos",
  both_body: "Esta carpeta contiene A LA VEZ el export HTML (messages.html) y el JSON oficial (result.json). Convertir de un formato al otro no aporta nada: ya tienes los dos. Lo único útil es ENRIQUECER el JSON oficial con los datos extra del HTML (se escribe result_enriched.json, sin tocar los originales). ¿Quieres enriquecer el JSON o cancelar la operación?",
  btn_enrich: "Enriquecer el JSON",
  convert_btn_tojson: "Convertir a JSON",
  convert_btn_tohtml: "Generar vista HTML",
  convert_btn_enrich: "Enriquecer JSON",
  job_converting: "Convirtiendo…",
  stage_convert: "Convirtiendo mensajes…",
  res_convert: "{msgs} mensajes",
  res_enriched: "{msgs} mensajes · {n} campos añadidos desde el HTML",
  pick_convert: "Elige la carpeta del export a convertir",
  open_json: "Abrir JSON",
  footer: "Telegram Export Studio · se ejecuta íntegramente en tu equipo"
},
en: {
  subtitle: "Merge, compact and enhance chat exports — 100% local",
  tab_fuse: "Merge", tab_compact: "Compact", tab_enhance: "Enhance",
  fuse_sources: "Source exports",
  fuse_empty: "Add or drag two or more Telegram export folders here<br>to merge them into one",
  add_folder: "Add folder",
  dest_pagination: "Destination & pagination",
  dest_label: "Destination folder",
  size_label: "Page size",
  chip_single: "Single file", chip_custom: "Custom",
  fuse_hint: "Telegram uses ~500 KB pages. Bigger pages = fewer messages.html files.",
  fuse_btn: "Merge exports", fuse_btn_n: "Merge {n} exports",
  compact_source: "Export to compact",
  compact_pick: "Choose or drag the export folder here<br>(a Telegram export or a merge result)",
  goal: "Target", mode_files: "Number of files", mode_size: "Size per page",
  files_hint: "1 = the whole history in a single messages.html",
  goal_hint_files: "You choose how many messages.html you want in the end (1 = the whole history in one) and the size of each page is computed automatically.",
  goal_hint_size: "You set the maximum size of each messages.html and the number of files follows from the total history.",
  srclink: "Open source · view on GitHub",
  inplace_hint: "Repaginated in place: only messages*.html files are rewritten; photos, videos and audio are untouched.",
  compact_btn: "Compact",
  enhance_source: "Export to enhance",
  enhance_pick: "Choose or drag the export folder here<br>(original, merged or compacted)",
  enhance_opts: "Viewing options",
  me_label: "Who are you? (the chosen person's messages get highlighted in a different color to mark them as yours)",
  me_none: "— Original design only, no own side —",
  layout_label: "Message layout",
  layout_both: "Both (switchable)", layout_chat: "Chat", layout_original: "Original",
  layout_hint_original: "Every avatar on the left, like the original Telegram export.",
  layout_hint_chat: "Avatars on both sides: theirs on the left, yours on the right.",
  layout_hint_both: "A floating button in the chat switches between both layouts.",
  feat_fullwidth: "Full width",
  feat_fullwidth_d: "Removes Telegram's centered column: the chat dynamically fills the whole screen",
  enhance_hint: "Telegram-style bubbles, reply quotes, light/dark theme, inline video/audio playback and a photo viewer. Compatible: you can still merge and compact this export afterwards.",
  enhance_btn: "Enhance export",
  need_me: "Choose who you are for chat mode",
  already_enhanced: "This export was already enhanced; it will be updated",
  item_sub: "{msgs} messages · {pages} page(s) · media {media}",
  item_more: "+{n} more",
  job_fusing: "Merging exports…", job_compacting: "Compacting export…",
  job_enhancing: "Enhancing export…",
  job_done: "Done", job_error: "Error",
  job_failed: "The operation failed",
  job_log: "Show full log",
  open_chat: "Open chat", open_folder: "Open folder",
  res_summary: "{msgs} messages in {pages} page(s)",
  res_range: "From {a} to {b} · ",
  res_own: " · {n} of your messages",
  snack_dup: "That export is already on the list",
  snack_need_output: "Set the destination folder",
  stage_scan: "Scanning {name}…", stage_merge: "Merging and deduplicating messages…",
  stage_media: "Copying media · {copied} files…",
  stage_write: "Writing pages…", stage_enhance: "Enhancing {name}…",
  stage_working: "Working…",
  pick_export: "Choose a Telegram export folder",
  pick_output: "Choose the destination folder",
  pick_compact: "Choose the export to compact",
  pick_enhance: "Choose the export to enhance",
  feat_bubbles: "Bubbles & chat background",
  feat_bubbles_d: "Real Telegram look, with configurable layout",
  feat_quotes: "Reply quotes",
  feat_quotes_d: "Box with author and snippet instead of \"In reply to this message\"",
  feat_theme: "Light / dark mode",
  feat_theme_d: "Floating button to switch theme while viewing the chat",
  feat_media: "Live media",
  feat_media_d: "Inline video and audio playback, photos in a viewer",
  feat_note: "Final note with instructions",
  feat_note_d: "Note at the end of the chat on how to use these tools",
  restore_btn: "Un-enhance",
  job_restoring: "Restoring the original design…",
  stage_restore: "Restoring {name}…",
  kind_group: "👥 Group · {n} participants",
  kind_private: "👤 Private chat",
  confirm_title: "Different chats?",
  confirm_mix: "“{a}” doesn't match “{b}”. These exports look like different chats and merging them would mix conversations. Add it anyway?",
  confirm_kind: "You are mixing a group chat with a private one. Add it anyway?",
  btn_cancel: "Cancel",
  btn_continue: "Add anyway",
  fuse_add_more: "Add or drag another export folder here",
  locating: "Locating “{name}” on disk…",
  locate_fail: "Couldn't locate the folder on disk. Add it once with the button and future drags from that location will work.",
  drop_only_folders: "Drop export folders, not files",
  drop_no_messages: "“{name}” doesn't contain messages.html",
  shutdown_tooltip: "Shut down the app",
  shutdown_title: "Shut down the app?",
  shutdown_body: "It will shut down and this tab will stop working, <strong><u>even if something is still in progress</u></strong>.",
  shutdown_confirm: "Shut down",
  shutdown_done_h: "App shut down",
  shutdown_done_p: "You can close this tab now. To use the app again, reopen the script or the executable.",
  tab_convert: "Converter",
  convert_source: "Folder to convert",
  convert_pick: "Choose or drag the export folder here<br>(HTML, JSON or both — detected automatically)",
  convert_opts: "Conversion options",
  convert_mode: "JSON conversion mode",
  mode_enriched: "Enriched (recommended)",
  mode_faithful: "Official format",
  mode_hint_enriched: "JSON with Telegram's official schema plus extra fields (call status texts, file names…) so nothing the HTML contains is lost.",
  mode_hint_faithful: "Only the keys of Telegram Desktop's official result.json, with no extra fields at all.",
  faithful_warn: "DESTRUCTIVE PROCESS: the official format drops the data it doesn't cover (call direction and status texts, file names…) and DELETES the messages*.html pages and web assets (css/, js/, images/) from the folder, so it ends up matching a real Telegram JSON export. If you need to keep that data or the HTML, use the enriched mode.",
  tojson_hint: "The result is saved as result.json inside the export folder itself — that way the media's relative paths (photos/…, voice_messages/…) keep working. If a result.json this tool didn't generate already exists, the operation stops so the official export is never overwritten.",
  tohtml_hint: "The browsable HTML view (messages*.html) will be generated in the same folder, including the web structure (css/js/images) the JSON export doesn't ship. Existing media is referenced as-is, never copied. Pre-existing messages*.html pages are never overwritten without confirmation.",
  enrich_hint: "Both formats will be combined: the official result.json (which holds data the HTML lacks: ids, edit dates, sizes…) gets enriched with the extra fields recoverable from the HTML. result_enriched.json is written without touching either original file.",
  eo_choice: "This folder only has an enriched JSON. What do you want to do?",
  eo_hint_tohtml: "The messages*.html pages and web assets (css/js/images) will be generated from this enriched JSON.",
  eo_hint_downgrade: "The fields and mark added by enrichment will be removed, leaving a result.json matching Telegram's official format.",
  downgrade_warn: "DESTRUCTIVE PROCESS: the extra data that only existed in the enriched JSON (call direction and status texts, file names…) will be lost and can't be recovered without the original HTML.",
  convert_btn_downgrade: "Downgrade to official format",
  res_removed_assets: " · HTML pages and assets removed",
  det_html: "Detected: HTML export → will be converted to JSON",
  det_json: "Detected: JSON export → the HTML view will be generated",
  det_both: "Detected: HTML + JSON at once → enrich the JSON",
  det_enriched_only: "Detected: enriched JSON only → choose what to do",
  both_title: "⚠ The folder contains BOTH formats",
  both_body: "This folder contains BOTH the HTML export (messages.html) and the official JSON (result.json) at the same time. Converting one into the other adds nothing: you already have both. The only useful operation is ENRICHING the official JSON with the HTML's extra data (result_enriched.json is written, originals untouched). Enrich the JSON or cancel the operation?",
  btn_enrich: "Enrich the JSON",
  convert_btn_tojson: "Convert to JSON",
  convert_btn_tohtml: "Generate HTML view",
  convert_btn_enrich: "Enrich JSON",
  job_converting: "Converting…",
  stage_convert: "Converting messages…",
  res_convert: "{msgs} messages",
  res_enriched: "{msgs} messages · {n} fields added from the HTML",
  pick_convert: "Choose the export folder to convert",
  open_json: "Open JSON",
  footer: "Telegram Export Studio · runs entirely on your machine"
},
fr: {
  subtitle: "Fusionnez, compactez et améliorez vos exports — 100 % local",
  tab_fuse: "Fusionner", tab_compact: "Compacter", tab_enhance: "Améliorer",
  fuse_sources: "Exports source",
  fuse_empty: "Ajoutez ou déposez ici deux dossiers d'export Telegram<br>ou plus pour les fusionner en un seul",
  add_folder: "Ajouter un dossier",
  dest_pagination: "Destination et pagination",
  dest_label: "Dossier de destination",
  size_label: "Taille de page",
  chip_single: "Fichier unique", chip_custom: "Personnalisé",
  fuse_hint: "Telegram utilise des pages d'environ 500 Ko. Pages plus grandes = moins de fichiers messages.html.",
  fuse_btn: "Fusionner les exports", fuse_btn_n: "Fusionner {n} exports",
  compact_source: "Export à compacter",
  compact_pick: "Choisissez ou déposez ici le dossier de l'export<br>(un export Telegram ou un résultat de fusion)",
  goal: "Objectif", mode_files: "Nombre de fichiers", mode_size: "Taille par page",
  files_hint: "1 = tout l'historique dans un seul messages.html",
  goal_hint_files: "Vous choisissez combien de messages.html il restera au final (1 = tout l'historique dans un seul) et la taille de chaque page se calcule automatiquement.",
  goal_hint_size: "Vous fixez la taille maximale de chaque messages.html et le nombre de fichiers en découle.",
  srclink: "Open source · voir sur GitHub",
  inplace_hint: "Repagination sur place : seuls les fichiers messages*.html sont réécrits ; photos, vidéos et audios restent intacts.",
  compact_btn: "Compacter",
  enhance_source: "Export à améliorer",
  enhance_pick: "Choisissez ou déposez ici le dossier de l'export<br>(original, fusionné ou compacté)",
  enhance_opts: "Options d'affichage",
  me_label: "Qui êtes-vous ? (les messages de la personne choisie seront mis en évidence d'une autre couleur pour indiquer qu'ils sont les vôtres)",
  me_none: "— Design original uniquement —",
  layout_label: "Disposition des messages",
  layout_both: "Les deux (commutable)", layout_chat: "Conversation", layout_original: "Original",
  layout_hint_original: "Tous les avatars à gauche, comme dans l'export original de Telegram.",
  layout_hint_chat: "Avatars des deux côtés : votre interlocuteur à gauche, vous à droite.",
  layout_hint_both: "Un bouton flottant dans le chat permet de basculer entre les deux dispositions.",
  feat_fullwidth: "Pleine largeur",
  feat_fullwidth_d: "Supprime la colonne centrée de Telegram : le chat occupe dynamiquement tout l'écran",
  enhance_hint: "Bulles style Telegram, citations de réponse, thème clair/sombre, lecture vidéo/audio intégrée et visionneuse photo. Compatible : vous pourrez toujours fusionner et compacter cet export ensuite.",
  enhance_btn: "Améliorer l'export",
  need_me: "Indiquez qui vous êtes pour le mode chat",
  already_enhanced: "Cet export était déjà amélioré ; il sera mis à jour",
  item_sub: "{msgs} messages · {pages} page(s) · médias {media}",
  item_more: "+{n} de plus",
  job_fusing: "Fusion des exports…", job_compacting: "Compactage de l'export…",
  job_enhancing: "Amélioration de l'export…",
  job_done: "Terminé", job_error: "Erreur",
  job_failed: "L'opération a échoué",
  job_log: "Voir le journal complet",
  open_chat: "Ouvrir le chat", open_folder: "Ouvrir le dossier",
  res_summary: "{msgs} messages sur {pages} page(s)",
  res_range: "Du {a} au {b} · ",
  res_own: " · {n} de vos messages",
  snack_dup: "Cet export est déjà dans la liste",
  snack_need_output: "Indiquez le dossier de destination",
  stage_scan: "Analyse de {name}…", stage_merge: "Fusion et déduplication des messages…",
  stage_media: "Copie des médias · {copied} fichiers…",
  stage_write: "Écriture des pages…", stage_enhance: "Amélioration de {name}…",
  stage_working: "Traitement…",
  pick_export: "Choisissez un dossier d'export Telegram",
  pick_output: "Choisissez le dossier de destination",
  pick_compact: "Choisissez l'export à compacter",
  pick_enhance: "Choisissez l'export à améliorer",
  feat_bubbles: "Bulles et fond de chat",
  feat_bubbles_d: "Apparence Telegram réelle, avec disposition configurable",
  feat_quotes: "Citations de réponse",
  feat_quotes_d: "Encadré avec auteur et extrait au lieu de \"In reply to this message\"",
  feat_theme: "Mode clair / sombre",
  feat_theme_d: "Bouton flottant pour changer de thème pendant la lecture",
  feat_media: "Médias intégrés",
  feat_media_d: "Lecture vidéo et audio en ligne, photos dans une visionneuse",
  feat_note: "Note finale avec instructions",
  feat_note_d: "Note à la fin du chat expliquant comment utiliser ces outils",
  restore_btn: "Rétablir l'original",
  job_restoring: "Restauration du design original…",
  stage_restore: "Restauration de {name}…",
  kind_group: "👥 Groupe · {n} participants",
  kind_private: "👤 Chat privé",
  confirm_title: "Chats différents ?",
  confirm_mix: "« {a} » ne correspond pas à « {b} ». Ces exports semblent provenir de chats différents et les fusionner mélangerait les conversations. L'ajouter quand même ?",
  confirm_kind: "Vous mélangez un chat de groupe avec un chat privé. L'ajouter quand même ?",
  btn_cancel: "Annuler",
  btn_continue: "Ajouter quand même",
  fuse_add_more: "Ajoutez ou déposez ici un autre dossier d'export",
  locating: "Localisation de « {name} » sur le disque…",
  locate_fail: "Impossible de localiser le dossier sur le disque. Ajoutez-le une fois avec le bouton et les prochains glisser-déposer depuis cet emplacement fonctionneront.",
  drop_only_folders: "Déposez des dossiers d'export, pas des fichiers",
  drop_no_messages: "« {name} » ne contient pas messages.html",
  shutdown_tooltip: "Éteindre l'application",
  shutdown_title: "Éteindre l'application ?",
  shutdown_body: "Elle s'éteindra et cet onglet cessera de fonctionner, <strong><u>même si une opération est en cours</u></strong>.",
  shutdown_confirm: "Éteindre",
  shutdown_done_h: "Application éteinte",
  shutdown_done_p: "Vous pouvez fermer cet onglet maintenant. Pour réutiliser l'application, rouvrez le script ou l'exécutable.",
  tab_convert: "Convertisseur",
  convert_source: "Dossier à convertir",
  convert_pick: "Choisissez ou déposez ici le dossier de l'export<br>(HTML, JSON ou les deux — détection automatique)",
  convert_opts: "Options de conversion",
  convert_mode: "Mode de conversion JSON",
  mode_enriched: "Enrichie (recommandée)",
  mode_faithful: "Format officiel",
  mode_hint_enriched: "JSON au schéma officiel de Telegram plus des champs extra (textes d'état des appels, noms de fichiers…) pour ne rien perdre de ce que contient le HTML.",
  mode_hint_faithful: "Uniquement les clés du result.json officiel de Telegram Desktop, sans aucun champ supplémentaire.",
  faithful_warn: "PROCESSUS DESTRUCTIF : le format officiel abandonne les données qu'il ne couvre pas (direction et texte d'état des appels, noms de fichiers…) et SUPPRIME les pages messages*.html et les ressources web (css/, js/, images/) du dossier, pour qu'il corresponde à un véritable export JSON de Telegram. Si vous devez conserver ces données ou le HTML, utilisez le mode enrichi.",
  tojson_hint: "Le résultat est enregistré comme result.json dans le dossier même de l'export — ainsi les chemins relatifs des médias (photos/…, voice_messages/…) continuent de fonctionner. Si un result.json non généré par cet outil existe déjà, l'opération s'arrête pour ne jamais écraser l'export officiel.",
  tohtml_hint: "La vue HTML navigable (messages*.html) sera générée dans le même dossier, avec la structure web (css/js/images) que l'export JSON n'inclut pas. Les médias existants sont référencés tels quels, jamais copiés. Les pages messages*.html déjà présentes ne sont jamais écrasées sans confirmation.",
  enrich_hint: "Les deux formats seront combinés : le result.json officiel (qui contient des données absentes du HTML : ids, dates d'édition, tailles…) sera enrichi avec les champs extra récupérables du HTML. result_enriched.json est écrit sans toucher aux fichiers originaux.",
  eo_choice: "Ce dossier ne contient qu'un JSON enrichi. Que voulez-vous faire ?",
  eo_hint_tohtml: "Les pages messages*.html et les ressources web (css/js/images) seront générées à partir de ce JSON enrichi.",
  eo_hint_downgrade: "Les champs et la marque ajoutés par l'enrichissement seront supprimés, pour obtenir un result.json conforme au format officiel de Telegram.",
  downgrade_warn: "PROCESSUS DESTRUCTIF : les données supplémentaires qui n'existaient que dans le JSON enrichi (direction et texte d'état des appels, noms de fichiers…) seront perdues et ne pourront pas être récupérées sans le HTML d'origine.",
  convert_btn_downgrade: "Revenir au format officiel",
  res_removed_assets: " · pages et ressources HTML supprimées",
  det_html: "Détecté : export HTML → sera converti en JSON",
  det_json: "Détecté : export JSON → la vue HTML sera générée",
  det_enriched_only: "Détecté : JSON enrichi uniquement → choisissez quoi faire",
  det_both: "Détecté : HTML + JSON à la fois → enrichir le JSON",
  both_title: "⚠ Le dossier contient LES DEUX formats",
  both_body: "Ce dossier contient À LA FOIS l'export HTML (messages.html) et le JSON officiel (result.json). Convertir l'un vers l'autre n'apporte rien : vous avez déjà les deux. La seule opération utile est d'ENRICHIR le JSON officiel avec les données extra du HTML (result_enriched.json est écrit, originaux intacts). Enrichir le JSON ou annuler l'opération ?",
  btn_enrich: "Enrichir le JSON",
  convert_btn_tojson: "Convertir en JSON",
  convert_btn_tohtml: "Générer la vue HTML",
  convert_btn_enrich: "Enrichir le JSON",
  job_converting: "Conversion…",
  stage_convert: "Conversion des messages…",
  res_convert: "{msgs} messages",
  res_enriched: "{msgs} messages · {n} champs ajoutés depuis le HTML",
  pick_convert: "Choisissez le dossier de l'export à convertir",
  open_json: "Ouvrir le JSON",
  footer: "Telegram Export Studio · s'exécute entièrement sur votre machine"
},
de: {
  subtitle: "Chat-Exporte zusammenführen, kompaktieren und verbessern — 100 % lokal",
  tab_fuse: "Zusammenführen", tab_compact: "Kompaktieren", tab_enhance: "Verbessern",
  fuse_sources: "Quell-Exporte",
  fuse_empty: "Füge zwei oder mehr Telegram-Export-Ordner hinzu oder ziehe sie hierher,<br>um sie zu einem zusammenzuführen",
  add_folder: "Ordner hinzufügen",
  dest_pagination: "Ziel & Seitenaufteilung",
  dest_label: "Zielordner",
  size_label: "Seitengröße",
  chip_single: "Einzelne Datei", chip_custom: "Benutzerdefiniert",
  fuse_hint: "Telegram verwendet ~500-KB-Seiten. Größere Seiten = weniger messages.html-Dateien.",
  fuse_btn: "Exporte zusammenführen", fuse_btn_n: "{n} Exporte zusammenführen",
  compact_source: "Zu kompaktierender Export",
  compact_pick: "Wähle den Export-Ordner oder ziehe ihn hierher<br>(ein Telegram-Export oder ein Zusammenführungs-Ergebnis)",
  goal: "Ziel", mode_files: "Anzahl der Dateien", mode_size: "Größe pro Seite",
  files_hint: "1 = gesamter Verlauf in einer einzigen messages.html",
  goal_hint_files: "Du wählst, wie viele messages.html am Ende übrig bleiben (1 = der gesamte Verlauf in einer), die Seitengröße wird automatisch berechnet.",
  goal_hint_size: "Du legst die maximale Größe jeder messages.html fest, die Dateianzahl ergibt sich aus dem Gesamtverlauf.",
  srclink: "Open Source · auf GitHub ansehen",
  inplace_hint: "Wird an Ort und Stelle neu paginiert: nur messages*.html werden neu geschrieben; Fotos, Videos und Audios bleiben unberührt.",
  compact_btn: "Kompaktieren",
  enhance_source: "Zu verbessernder Export",
  enhance_pick: "Wähle den Export-Ordner oder ziehe ihn hierher<br>(original, zusammengeführt oder kompaktiert)",
  enhance_opts: "Anzeigeoptionen",
  me_label: "Wer bist du? (die Nachrichten der gewählten Person werden andersfarbig hervorgehoben, um sie als deine zu kennzeichnen)",
  me_none: "— Nur Original-Design —",
  layout_label: "Nachrichten-Layout",
  layout_both: "Beide (umschaltbar)", layout_chat: "Chat", layout_original: "Original",
  layout_hint_original: "Alle Avatare links, wie im ursprünglichen Telegram-Export.",
  layout_hint_chat: "Avatare auf beiden Seiten: dein Gegenüber links, du rechts.",
  layout_hint_both: "Ein schwebender Button im Chat wechselt zwischen beiden Layouts.",
  feat_fullwidth: "Volle Breite",
  feat_fullwidth_d: "Entfernt Telegrams zentrierte Spalte: der Chat füllt dynamisch den ganzen Bildschirm",
  enhance_hint: "Telegram-Sprechblasen, Antwort-Zitate, helles/dunkles Design, eingebettete Video-/Audio-Wiedergabe und Foto-Viewer. Kompatibel: du kannst diesen Export danach weiterhin zusammenführen und kompaktieren.",
  enhance_btn: "Export verbessern",
  need_me: "Wähle für den Chat-Modus, wer du bist",
  already_enhanced: "Dieser Export war bereits verbessert; er wird aktualisiert",
  item_sub: "{msgs} Nachrichten · {pages} Seite(n) · Medien {media}",
  item_more: "+{n} weitere",
  job_fusing: "Exporte werden zusammengeführt…", job_compacting: "Export wird kompaktiert…",
  job_enhancing: "Export wird verbessert…",
  job_done: "Fertig", job_error: "Fehler",
  job_failed: "Der Vorgang ist fehlgeschlagen",
  job_log: "Vollständiges Protokoll anzeigen",
  open_chat: "Chat öffnen", open_folder: "Ordner öffnen",
  res_summary: "{msgs} Nachrichten auf {pages} Seite(n)",
  res_range: "Vom {a} bis {b} · ",
  res_own: " · {n} eigene Nachrichten",
  snack_dup: "Dieser Export ist bereits in der Liste",
  snack_need_output: "Gib den Zielordner an",
  stage_scan: "Scanne {name}…", stage_merge: "Nachrichten werden zusammengeführt und dedupliziert…",
  stage_media: "Medien werden kopiert · {copied} Dateien…",
  stage_write: "Seiten werden geschrieben…", stage_enhance: "Verbessere {name}…",
  stage_working: "Verarbeitung…",
  pick_export: "Wähle einen Telegram-Export-Ordner",
  pick_output: "Wähle den Zielordner",
  pick_compact: "Wähle den zu kompaktierenden Export",
  pick_enhance: "Wähle den zu verbessernden Export",
  feat_bubbles: "Sprechblasen & Chat-Hintergrund",
  feat_bubbles_d: "Echtes Telegram-Aussehen mit konfigurierbarem Layout",
  feat_quotes: "Antwort-Zitate",
  feat_quotes_d: "Kasten mit Autor und Ausschnitt statt \"In reply to this message\"",
  feat_theme: "Heller / dunkler Modus",
  feat_theme_d: "Schwebender Button zum Umschalten des Themas beim Lesen",
  feat_media: "Eingebettete Medien",
  feat_media_d: "Video- und Audio-Wiedergabe inline, Fotos im Viewer",
  feat_note: "Abschlussnotiz mit Anleitung",
  feat_note_d: "Notiz am Chat-Ende zur Nutzung dieser Werkzeuge",
  restore_btn: "Zurücksetzen",
  job_restoring: "Original-Design wird wiederhergestellt…",
  stage_restore: "Stelle {name} wieder her…",
  kind_group: "👥 Gruppe · {n} Teilnehmer",
  kind_private: "👤 Privater Chat",
  confirm_title: "Verschiedene Chats?",
  confirm_mix: "„{a}“ stimmt nicht mit „{b}“ überein. Diese Exporte scheinen von verschiedenen Chats zu stammen; ein Zusammenführen würde Unterhaltungen vermischen. Trotzdem hinzufügen?",
  confirm_kind: "Du mischst einen Gruppen-Chat mit einem privaten Chat. Trotzdem hinzufügen?",
  btn_cancel: "Abbrechen",
  btn_continue: "Trotzdem hinzufügen",
  fuse_add_more: "Füge einen weiteren Export-Ordner hinzu oder ziehe ihn hierher",
  locating: "Suche „{name}“ auf der Festplatte…",
  locate_fail: "Der Ordner konnte auf der Festplatte nicht gefunden werden. Füge ihn einmal über den Button hinzu; künftiges Ziehen von diesem Ort funktioniert dann.",
  drop_only_folders: "Ziehe Export-Ordner hierher, keine Dateien",
  drop_no_messages: "„{name}“ enthält keine messages.html",
  shutdown_tooltip: "App beenden",
  shutdown_title: "App beenden?",
  shutdown_body: "Sie wird beendet und dieser Tab funktioniert danach nicht mehr, <strong><u>auch wenn gerade etwas läuft</u></strong>.",
  shutdown_confirm: "Beenden",
  shutdown_done_h: "App beendet",
  shutdown_done_p: "Du kannst diesen Tab jetzt schließen. Um die App erneut zu nutzen, öffne das Skript oder die ausführbare Datei erneut.",
  tab_convert: "Konverter",
  convert_source: "Zu konvertierender Ordner",
  convert_pick: "Wähle oder ziehe den Export-Ordner hierher<br>(HTML, JSON oder beides — wird automatisch erkannt)",
  convert_opts: "Konvertierungsoptionen",
  convert_mode: "JSON-Konvertierungsmodus",
  mode_enriched: "Angereichert (empfohlen)",
  mode_faithful: "Offizielles Format",
  mode_hint_enriched: "JSON mit Telegrams offiziellem Schema plus Zusatzfeldern (Anruf-Statustexte, Dateinamen…), damit nichts aus dem HTML verloren geht.",
  mode_hint_faithful: "Nur die Schlüssel der offiziellen result.json von Telegram Desktop, ohne jegliche Zusatzfelder.",
  faithful_warn: "DESTRUKTIVER VORGANG: Das offizielle Format verwirft die Daten, die es nicht abdeckt (Richtung und Statustext von Anrufen, Dateinamen…) und LÖSCHT die messages*.html-Seiten sowie die Web-Ressourcen (css/, js/, images/) aus dem Ordner, damit er genau wie ein echter Telegram-JSON-Export aussieht. Wenn du diese Daten oder das HTML brauchst, nutze den angereicherten Modus.",
  tojson_hint: "Das Ergebnis wird als result.json im Export-Ordner selbst gespeichert — so funktionieren die relativen Medienpfade (photos/…, voice_messages/…) weiterhin. Existiert bereits eine result.json, die nicht von diesem Tool stammt, stoppt der Vorgang, damit der offizielle Export nie überschrieben wird.",
  tohtml_hint: "Die durchsuchbare HTML-Ansicht (messages*.html) wird im selben Ordner erzeugt, inklusive der Web-Struktur (css/js/images), die der JSON-Export nicht mitbringt. Vorhandene Medien werden unverändert referenziert, nie kopiert. Bereits vorhandene messages*.html-Seiten werden nie ohne Bestätigung überschrieben.",
  enrich_hint: "Beide Formate werden kombiniert: die offizielle result.json (die Daten enthält, die dem HTML fehlen: IDs, Bearbeitungsdaten, Größen…) wird mit den aus dem HTML rekonstruierbaren Zusatzfeldern angereichert. result_enriched.json wird geschrieben, ohne die Originaldateien anzutasten.",
  eo_choice: "Dieser Ordner enthält nur eine angereicherte JSON-Datei. Was möchtest du tun?",
  eo_hint_tohtml: "Die messages*.html-Seiten und Web-Ressourcen (css/js/images) werden aus dieser angereicherten JSON-Datei erzeugt.",
  eo_hint_downgrade: "Die durch die Anreicherung hinzugefügten Felder und die Markierung werden entfernt, sodass eine result.json entsprechend dem offiziellen Telegram-Format entsteht.",
  downgrade_warn: "DESTRUKTIVER VORGANG: Die Zusatzdaten, die nur in der angereicherten JSON-Datei existierten (Richtung und Statustext von Anrufen, Dateinamen…), gehen verloren und können ohne das ursprüngliche HTML nicht wiederhergestellt werden.",
  convert_btn_downgrade: "Auf offizielles Format zurückstufen",
  res_removed_assets: " · HTML-Seiten und -Ressourcen entfernt",
  det_html: "Erkannt: HTML-Export → wird in JSON konvertiert",
  det_json: "Erkannt: JSON-Export → die HTML-Ansicht wird erzeugt",
  det_enriched_only: "Erkannt: nur angereichertes JSON → wähle, was zu tun ist",
  det_both: "Erkannt: HTML + JSON zugleich → JSON anreichern",
  both_title: "⚠ Der Ordner enthält BEIDE Formate",
  both_body: "Dieser Ordner enthält GLEICHZEITIG den HTML-Export (messages.html) und das offizielle JSON (result.json). Eine Konvertierung bringt nichts: beides ist schon da. Sinnvoll ist nur, das offizielle JSON mit den Zusatzdaten des HTML ANZUREICHERN (result_enriched.json wird geschrieben, Originale bleiben unberührt). JSON anreichern oder Vorgang abbrechen?",
  btn_enrich: "JSON anreichern",
  convert_btn_tojson: "In JSON konvertieren",
  convert_btn_tohtml: "HTML-Ansicht erzeugen",
  convert_btn_enrich: "JSON anreichern",
  job_converting: "Konvertiere…",
  stage_convert: "Konvertiere Nachrichten…",
  res_convert: "{msgs} Nachrichten",
  res_enriched: "{msgs} Nachrichten · {n} Felder aus dem HTML ergänzt",
  pick_convert: "Wähle den zu konvertierenden Export-Ordner",
  open_json: "JSON öffnen",
  footer: "Telegram Export Studio · läuft vollständig auf deinem Rechner"
},
pt: {
  subtitle: "Mescle, compacte e melhore exports de chats — 100% local",
  tab_fuse: "Mesclar", tab_compact: "Compactar", tab_enhance: "Melhorar",
  fuse_sources: "Exports de origem",
  fuse_empty: "Adicione ou arraste aqui duas ou mais pastas de export<br>do Telegram para mesclá-las em uma só",
  add_folder: "Adicionar pasta",
  dest_pagination: "Destino e paginação",
  dest_label: "Pasta de destino",
  size_label: "Tamanho da página",
  chip_single: "Arquivo único", chip_custom: "Personalizado",
  fuse_hint: "O Telegram usa páginas de ~500 KB. Páginas maiores = menos arquivos messages.html.",
  fuse_btn: "Mesclar exports", fuse_btn_n: "Mesclar {n} exports",
  compact_source: "Export a compactar",
  compact_pick: "Escolha ou arraste aqui a pasta do export<br>(um export do Telegram ou o resultado de uma mesclagem)",
  goal: "Objetivo", mode_files: "Número de arquivos", mode_size: "Tamanho por página",
  files_hint: "1 = todo o histórico em um único messages.html",
  goal_hint_files: "Você escolhe quantos messages.html quer no final (1 = todo o histórico em um só) e o tamanho de cada página é calculado automaticamente.",
  goal_hint_size: "Você define o tamanho máximo de cada messages.html e o número de arquivos resulta do histórico total.",
  srclink: "Código aberto · ver no GitHub",
  inplace_hint: "Repaginado no local: apenas os messages*.html são reescritos; fotos, vídeos e áudios não são tocados.",
  compact_btn: "Compactar",
  enhance_source: "Export a melhorar",
  enhance_pick: "Escolha ou arraste aqui a pasta do export<br>(original, mesclado ou compactado)",
  enhance_opts: "Opções de visualização",
  me_label: "Quem é você? (as mensagens da pessoa escolhida serão destacadas com outra cor para indicar que são suas)",
  me_none: "— Apenas design original —",
  layout_label: "Disposição das mensagens",
  layout_both: "Ambos (alternável)", layout_chat: "Conversa", layout_original: "Original",
  layout_hint_original: "Todos os avatares à esquerda, como no export original do Telegram.",
  layout_hint_chat: "Avatares dos dois lados: o interlocutor à esquerda e você à direita.",
  layout_hint_both: "Um botão flutuante no chat alterna entre as duas disposições.",
  feat_fullwidth: "Largura total",
  feat_fullwidth_d: "Remove a coluna centralizada do Telegram: o chat preenche dinamicamente a tela inteira",
  enhance_hint: "Balões estilo Telegram, citações de resposta, tema claro/escuro, vídeo/áudio embutidos e visualizador de fotos. Compatível: você ainda poderá mesclar e compactar este export depois.",
  enhance_btn: "Melhorar export",
  need_me: "Escolha quem é você para o modo chat",
  already_enhanced: "Este export já estava melhorado; será atualizado",
  item_sub: "{msgs} mensagens · {pages} página(s) · mídia {media}",
  item_more: "+{n} mais",
  job_fusing: "Mesclando exports…", job_compacting: "Compactando export…",
  job_enhancing: "Melhorando export…",
  job_done: "Concluído", job_error: "Erro",
  job_failed: "A operação falhou",
  job_log: "Ver registro completo",
  open_chat: "Abrir chat", open_folder: "Abrir pasta",
  res_summary: "{msgs} mensagens em {pages} página(s)",
  res_range: "De {a} até {b} · ",
  res_own: " · {n} mensagens suas",
  snack_dup: "Esse export já está na lista",
  snack_need_output: "Indique a pasta de destino",
  stage_scan: "Escaneando {name}…", stage_merge: "Mesclando e deduplicando mensagens…",
  stage_media: "Copiando mídia · {copied} arquivos…",
  stage_write: "Escrevendo páginas…", stage_enhance: "Melhorando {name}…",
  stage_working: "Processando…",
  pick_export: "Escolha uma pasta de export do Telegram",
  pick_output: "Escolha a pasta de destino",
  pick_compact: "Escolha o export a compactar",
  pick_enhance: "Escolha o export a melhorar",
  feat_bubbles: "Balões e fundo de chat",
  feat_bubbles_d: "Aparência real do Telegram, com disposição configurável",
  feat_quotes: "Citações de resposta",
  feat_quotes_d: "Caixa com autor e trecho em vez de \"In reply to this message\"",
  feat_theme: "Modo claro / escuro",
  feat_theme_d: "Botão flutuante para trocar o tema durante a leitura",
  feat_media: "Mídia ao vivo",
  feat_media_d: "Vídeos e áudios reproduzíveis inline, fotos em visualizador",
  feat_note: "Mensagem final com instruções",
  feat_note_d: "Nota no fim do chat sobre como usar estas ferramentas",
  restore_btn: "Reverter melhorias",
  job_restoring: "Restaurando o design original…",
  stage_restore: "Restaurando {name}…",
  kind_group: "👥 Grupo · {n} participantes",
  kind_private: "👤 Chat privado",
  confirm_title: "Chats diferentes?",
  confirm_mix: "“{a}” não corresponde a “{b}”. Estes exports parecem de chats diferentes e mesclá-los misturaria conversas. Adicionar mesmo assim?",
  confirm_kind: "Você está misturando um chat de grupo com um privado. Adicionar mesmo assim?",
  btn_cancel: "Cancelar",
  btn_continue: "Adicionar mesmo assim",
  fuse_add_more: "Adicione ou arraste aqui outra pasta de export",
  locating: "Localizando “{name}” no disco…",
  locate_fail: "Não foi possível localizar a pasta no disco. Adicione-a uma vez com o botão e os próximos arrastos desse local funcionarão.",
  drop_only_folders: "Solte pastas de export, não arquivos",
  drop_no_messages: "“{name}” não contém messages.html",
  shutdown_tooltip: "Desligar o aplicativo",
  shutdown_title: "Desligar o aplicativo?",
  shutdown_body: "Ele será desligado e esta aba deixará de funcionar, <strong><u>mesmo que algo ainda esteja em andamento</u></strong>.",
  shutdown_confirm: "Desligar",
  shutdown_done_h: "Aplicativo desligado",
  shutdown_done_p: "Você já pode fechar esta aba. Para usar o app novamente, abra outra vez o script ou o executável.",
  tab_convert: "Conversor",
  convert_source: "Pasta a converter",
  convert_pick: "Escolha ou arraste aqui a pasta do export<br>(HTML, JSON ou ambos — detetado automaticamente)",
  convert_opts: "Opções de conversão",
  convert_mode: "Modo de conversão para JSON",
  mode_enriched: "Enriquecida (recomendada)",
  mode_faithful: "Formato oficial",
  mode_hint_enriched: "JSON com o esquema oficial do Telegram mais campos extra (textos de estado das chamadas, nomes de ficheiros…) para não perder nada do que o HTML contém.",
  mode_hint_faithful: "Apenas as chaves do result.json oficial do Telegram Desktop, sem nenhum campo extra.",
  faithful_warn: "PROCESSO DESTRUTIVO: o formato oficial descarta os dados que não contempla (direção e texto de estado das chamadas, nomes de ficheiros…) e APAGA as páginas messages*.html e os recursos web (css/, js/, images/) da pasta, para que fique igual a um export JSON real do Telegram. Se precisar de manter esses dados ou o HTML, use o modo enriquecido.",
  tojson_hint: "O resultado é guardado como result.json dentro da própria pasta do export — assim os caminhos relativos da média (photos/…, voice_messages/…) continuam a funcionar. Se já existir um result.json que esta ferramenta não gerou, a operação para, para nunca sobrescrever o export oficial.",
  tohtml_hint: "A vista HTML navegável (messages*.html) será gerada na mesma pasta, incluindo a estrutura web (css/js/images) que o export JSON não traz. A média existente é referenciada tal como está, nunca copiada. Páginas messages*.html já existentes nunca são sobrescritas sem confirmação.",
  enrich_hint: "Os dois formatos serão combinados: o result.json oficial (que contém dados que o HTML não tem: ids, datas de edição, tamanhos…) será enriquecido com os campos extra recuperáveis do HTML. result_enriched.json é escrito sem tocar em nenhum dos ficheiros originais.",
  eo_choice: "Esta pasta só tem um JSON enriquecido. O que quer fazer?",
  eo_hint_tohtml: "As páginas messages*.html e os recursos web (css/js/images) serão gerados a partir deste JSON enriquecido.",
  eo_hint_downgrade: "Os campos e a marca adicionados pelo enriquecimento serão removidos, deixando um result.json igual ao formato oficial do Telegram.",
  downgrade_warn: "PROCESSO DESTRUTIVO: os dados extra que só existiam no JSON enriquecido (direção e texto de estado das chamadas, nomes de ficheiros…) serão perdidos e não poderão ser recuperados sem o HTML original.",
  convert_btn_downgrade: "Reverter para formato oficial",
  res_removed_assets: " · páginas e recursos HTML removidos",
  det_html: "Detetado: export HTML → será convertido para JSON",
  det_json: "Detetado: export JSON → será gerada a vista HTML",
  det_enriched_only: "Detetado: apenas JSON enriquecido → escolha o que fazer",
  det_both: "Detetado: HTML + JSON ao mesmo tempo → enriquecer o JSON",
  both_title: "⚠ A pasta contém AMBOS os formatos",
  both_body: "Esta pasta contém AO MESMO TEMPO o export HTML (messages.html) e o JSON oficial (result.json). Converter um no outro não acrescenta nada: já tem os dois. A única operação útil é ENRIQUECER o JSON oficial com os dados extra do HTML (escreve-se result_enriched.json, sem tocar nos originais). Enriquecer o JSON ou cancelar a operação?",
  btn_enrich: "Enriquecer o JSON",
  convert_btn_tojson: "Converter para JSON",
  convert_btn_tohtml: "Gerar vista HTML",
  convert_btn_enrich: "Enriquecer JSON",
  job_converting: "A converter…",
  stage_convert: "A converter mensagens…",
  res_convert: "{msgs} mensagens",
  res_enriched: "{msgs} mensagens · {n} campos adicionados do HTML",
  pick_convert: "Escolha a pasta do export a converter",
  open_json: "Abrir JSON",
  footer: "Telegram Export Studio · roda inteiramente no seu computador"
},
it: {
  subtitle: "Unisci, compatta e migliora gli export delle chat — 100% locale",
  tab_fuse: "Unisci", tab_compact: "Compatta", tab_enhance: "Migliora",
  fuse_sources: "Export di origine",
  fuse_empty: "Aggiungi o trascina qui due o più cartelle di export<br>di Telegram per unirle in una sola",
  add_folder: "Aggiungi cartella",
  dest_pagination: "Destinazione e paginazione",
  dest_label: "Cartella di destinazione",
  size_label: "Dimensione pagina",
  chip_single: "File unico", chip_custom: "Personalizzato",
  fuse_hint: "Telegram usa pagine di ~500 KB. Pagine più grandi = meno file messages.html.",
  fuse_btn: "Unisci export", fuse_btn_n: "Unisci {n} export",
  compact_source: "Export da compattare",
  compact_pick: "Scegli o trascina qui la cartella dell'export<br>(un export di Telegram o il risultato di un'unione)",
  goal: "Obiettivo", mode_files: "Numero di file", mode_size: "Dimensione per pagina",
  files_hint: "1 = tutta la cronologia in un unico messages.html",
  goal_hint_files: "Scegli quanti messages.html vuoi alla fine (1 = tutta la cronologia in uno) e la dimensione di ogni pagina viene calcolata da sola.",
  goal_hint_size: "Fissi la dimensione massima di ogni messages.html e il numero di file dipende dalla cronologia totale.",
  srclink: "Open source · vedi su GitHub",
  inplace_hint: "Ripaginato sul posto: vengono riscritti solo i messages*.html; foto, video e audio restano intatti.",
  compact_btn: "Compatta",
  enhance_source: "Export da migliorare",
  enhance_pick: "Scegli o trascina qui la cartella dell'export<br>(originale, unito o compattato)",
  enhance_opts: "Opzioni di visualizzazione",
  me_label: "Chi sei tu? (i messaggi della persona scelta verranno evidenziati con un altro colore per indicare che sono tuoi)",
  me_none: "— Solo design originale —",
  layout_label: "Disposizione dei messaggi",
  layout_both: "Entrambi (commutabile)", layout_chat: "Conversazione", layout_original: "Originale",
  layout_hint_original: "Tutti gli avatar a sinistra, come nell'export originale di Telegram.",
  layout_hint_chat: "Avatar su entrambi i lati: l'interlocutore a sinistra e tu a destra.",
  layout_hint_both: "Un pulsante flottante nella chat alterna tra le due disposizioni.",
  feat_fullwidth: "Larghezza piena",
  feat_fullwidth_d: "Rimuove la colonna centrata di Telegram: la chat riempie dinamicamente tutto lo schermo",
  enhance_hint: "Bolle in stile Telegram, citazioni delle risposte, tema chiaro/scuro, riproduzione video/audio integrata e visualizzatore foto. Compatibile: potrai comunque unire e compattare questo export in seguito.",
  enhance_btn: "Migliora export",
  need_me: "Scegli chi sei per la modalità chat",
  already_enhanced: "Questo export era già migliorato; verrà aggiornato",
  item_sub: "{msgs} messaggi · {pages} pagina/e · media {media}",
  item_more: "+{n} altri",
  job_fusing: "Unione degli export…", job_compacting: "Compattazione dell'export…",
  job_enhancing: "Miglioramento dell'export…",
  job_done: "Completato", job_error: "Errore",
  job_failed: "L'operazione non è riuscita",
  job_log: "Mostra registro completo",
  open_chat: "Apri chat", open_folder: "Apri cartella",
  res_summary: "{msgs} messaggi in {pages} pagina/e",
  res_range: "Dal {a} al {b} · ",
  res_own: " · {n} tuoi messaggi",
  snack_dup: "Quell'export è già nella lista",
  snack_need_output: "Indica la cartella di destinazione",
  stage_scan: "Scansione di {name}…", stage_merge: "Unione e deduplicazione dei messaggi…",
  stage_media: "Copia dei media · {copied} file…",
  stage_write: "Scrittura delle pagine…", stage_enhance: "Miglioramento di {name}…",
  stage_working: "Elaborazione…",
  pick_export: "Scegli una cartella di export di Telegram",
  pick_output: "Scegli la cartella di destinazione",
  pick_compact: "Scegli l'export da compattare",
  pick_enhance: "Scegli l'export da migliorare",
  feat_bubbles: "Bolle e sfondo chat",
  feat_bubbles_d: "Aspetto reale di Telegram, con disposizione configurabile",
  feat_quotes: "Citazioni delle risposte",
  feat_quotes_d: "Riquadro con autore ed estratto invece di \"In reply to this message\"",
  feat_theme: "Modalità chiara / scura",
  feat_theme_d: "Pulsante flottante per cambiare tema durante la lettura",
  feat_media: "Media dal vivo",
  feat_media_d: "Video e audio riproducibili inline, foto nel visualizzatore",
  feat_note: "Messaggio finale con istruzioni",
  feat_note_d: "Nota alla fine della chat su come usare questi strumenti",
  restore_btn: "Ripristina originale",
  job_restoring: "Ripristino del design originale…",
  stage_restore: "Ripristino di {name}…",
  kind_group: "👥 Gruppo · {n} partecipanti",
  kind_private: "👤 Chat privata",
  confirm_title: "Chat diverse?",
  confirm_mix: "«{a}» non corrisponde a «{b}». Questi export sembrano di chat diverse e unirli mescolerebbe le conversazioni. Aggiungerlo comunque?",
  confirm_kind: "Stai mescolando una chat di gruppo con una privata. Aggiungerla comunque?",
  btn_cancel: "Annulla",
  btn_continue: "Aggiungi comunque",
  fuse_add_more: "Aggiungi o trascina qui un'altra cartella di export",
  locating: "Localizzazione di «{name}» sul disco…",
  locate_fail: "Impossibile localizzare la cartella sul disco. Aggiungila una volta con il pulsante e i prossimi trascinamenti da quella posizione funzioneranno.",
  drop_only_folders: "Trascina cartelle di export, non file",
  drop_no_messages: "«{name}» non contiene messages.html",
  shutdown_tooltip: "Spegni l'app",
  shutdown_title: "Spegnere l'app?",
  shutdown_body: "Si spegnerà e questa scheda smetterà di funzionare, <strong><u>anche se c'è qualcosa in corso</u></strong>.",
  shutdown_confirm: "Spegni",
  shutdown_done_h: "App spenta",
  shutdown_done_p: "Ora puoi chiudere questa scheda. Per riutilizzare l'app, riapri lo script o l'eseguibile.",
  tab_convert: "Convertitore",
  convert_source: "Cartella da convertire",
  convert_pick: "Scegli o trascina qui la cartella dell'export<br>(HTML, JSON o entrambi — rilevato automaticamente)",
  convert_opts: "Opzioni di conversione",
  convert_mode: "Modalità di conversione JSON",
  mode_enriched: "Arricchita (consigliata)",
  mode_faithful: "Formato ufficiale",
  mode_hint_enriched: "JSON con lo schema ufficiale di Telegram più campi extra (testi di stato delle chiamate, nomi dei file…) per non perdere nulla di ciò che contiene l'HTML.",
  mode_hint_faithful: "Solo le chiavi del result.json ufficiale di Telegram Desktop, senza alcun campo extra.",
  faithful_warn: "PROCESSO DISTRUTTIVO: il formato ufficiale scarta i dati che non prevede (direzione e testo di stato delle chiamate, nomi dei file…) ed ELIMINA le pagine messages*.html e le risorse web (css/, js/, images/) dalla cartella, così che risulti identica a un vero export JSON di Telegram. Se ti servono quei dati o l'HTML, usa la modalità arricchita.",
  tojson_hint: "Il risultato viene salvato come result.json dentro la cartella stessa dell'export — così i percorsi relativi dei media (photos/…, voice_messages/…) continuano a funzionare. Se esiste già un result.json non generato da questo strumento, l'operazione si ferma per non sovrascrivere mai l'export ufficiale.",
  tohtml_hint: "La vista HTML navigabile (messages*.html) sarà generata nella stessa cartella, inclusa la struttura web (css/js/images) che l'export JSON non contiene. I media esistenti vengono referenziati così come sono, mai copiati. Le pagine messages*.html già presenti non vengono mai sovrascritte senza conferma.",
  enrich_hint: "I due formati verranno combinati: il result.json ufficiale (che contiene dati assenti nell'HTML: id, date di modifica, dimensioni…) sarà arricchito con i campi extra recuperabili dall'HTML. result_enriched.json viene scritto senza toccare nessuno dei file originali.",
  eo_choice: "Questa cartella contiene solo un JSON arricchito. Cosa vuoi fare?",
  eo_hint_tohtml: "Le pagine messages*.html e le risorse web (css/js/images) verranno generate a partire da questo JSON arricchito.",
  eo_hint_downgrade: "I campi e il marcatore aggiunti dall'arricchimento verranno rimossi, lasciando un result.json identico al formato ufficiale di Telegram.",
  downgrade_warn: "PROCESSO DISTRUTTIVO: i dati extra presenti solo nel JSON arricchito (direzione e testo di stato delle chiamate, nomi dei file…) andranno persi e non potranno essere recuperati senza l'HTML originale.",
  convert_btn_downgrade: "Torna al formato ufficiale",
  res_removed_assets: " · pagine e risorse HTML rimosse",
  det_html: "Rilevato: export HTML → sarà convertito in JSON",
  det_json: "Rilevato: export JSON → sarà generata la vista HTML",
  det_enriched_only: "Rilevato: solo JSON arricchito → scegli cosa fare",
  det_both: "Rilevato: HTML + JSON insieme → arricchire il JSON",
  both_title: "⚠ La cartella contiene ENTRAMBI i formati",
  both_body: "Questa cartella contiene CONTEMPORANEAMENTE l'export HTML (messages.html) e il JSON ufficiale (result.json). Convertire l'uno nell'altro non aggiunge nulla: li hai già entrambi. L'unica operazione utile è ARRICCHIRE il JSON ufficiale con i dati extra dell'HTML (viene scritto result_enriched.json, originali intatti). Arricchire il JSON o annullare l'operazione?",
  btn_enrich: "Arricchisci il JSON",
  convert_btn_tojson: "Converti in JSON",
  convert_btn_tohtml: "Genera vista HTML",
  convert_btn_enrich: "Arricchisci JSON",
  job_converting: "Conversione…",
  stage_convert: "Conversione dei messaggi…",
  res_convert: "{msgs} messaggi",
  res_enriched: "{msgs} messaggi · {n} campi aggiunti dall'HTML",
  pick_convert: "Scegli la cartella dell'export da convertire",
  open_json: "Apri JSON",
  footer: "Telegram Export Studio · gira interamente sul tuo computer"
},
ru: {
  subtitle: "Объединяйте, сжимайте и улучшайте экспорты чатов — 100% локально",
  tab_fuse: "Объединить", tab_compact: "Сжать", tab_enhance: "Улучшить",
  fuse_sources: "Исходные экспорты",
  fuse_empty: "Добавьте или перетащите сюда две или более папки<br>экспорта Telegram, чтобы объединить их в одну",
  add_folder: "Добавить папку",
  dest_pagination: "Назначение и разбиение",
  dest_label: "Папка назначения",
  size_label: "Размер страницы",
  chip_single: "Один файл", chip_custom: "Свой размер",
  fuse_hint: "Telegram использует страницы ~500 КБ. Чем больше страницы, тем меньше файлов messages.html.",
  fuse_btn: "Объединить экспорты", fuse_btn_n: "Объединить {n} экспорта(ов)",
  compact_source: "Экспорт для сжатия",
  compact_pick: "Выберите или перетащите сюда папку экспорта<br>(подойдёт экспорт Telegram или результат объединения)",
  goal: "Цель", mode_files: "Число файлов", mode_size: "Размер страницы",
  files_hint: "1 = вся история в одном messages.html",
  goal_hint_files: "Вы выбираете, сколько messages.html останется в итоге (1 = вся история в одном), а размер страниц рассчитывается автоматически.",
  goal_hint_size: "Вы задаёте максимальный размер каждого messages.html, а число файлов зависит от всей истории.",
  srclink: "Открытый код · смотреть на GitHub",
  inplace_hint: "Разбиение выполняется на месте: перезаписываются только messages*.html; фото, видео и аудио не затрагиваются.",
  compact_btn: "Сжать",
  enhance_source: "Экспорт для улучшения",
  enhance_pick: "Выберите или перетащите сюда папку экспорта<br>(оригинал, объединённый или сжатый)",
  enhance_opts: "Параметры отображения",
  me_label: "Кто вы? (сообщения выбранного человека будут выделены другим цветом, чтобы отметить их как ваши)",
  me_none: "— Только оригинальный дизайн, без своей стороны —",
  layout_label: "Расположение сообщений",
  layout_both: "Оба (переключаемо)", layout_chat: "Диалог", layout_original: "Оригинал",
  layout_hint_original: "Все аватары слева, как в исходном экспорте Telegram.",
  layout_hint_chat: "Аватары с обеих сторон: собеседник слева, вы справа.",
  layout_hint_both: "Плавающая кнопка в чате переключает между двумя вариантами.",
  feat_fullwidth: "Во всю ширину",
  feat_fullwidth_d: "Убирает центральную колонку Telegram: чат динамически занимает весь экран",
  enhance_hint: "Пузыри в стиле Telegram, цитаты ответов, светлая/тёмная тема, воспроизводимые видео и аудио, просмотр фото. Совместимо: этот экспорт можно будет объединять и сжимать дальше.",
  enhance_btn: "Улучшить экспорт",
  need_me: "Укажите, кто вы, для режима диалога",
  already_enhanced: "Этот экспорт уже был улучшен; он будет обновлён",
  item_sub: "{msgs} сообщений · {pages} страниц(ы) · медиа {media}",
  item_more: "ещё +{n}",
  job_fusing: "Объединение экспортов…", job_compacting: "Сжатие экспорта…",
  job_enhancing: "Улучшение экспорта…",
  job_done: "Готово", job_error: "Ошибка",
  job_failed: "Операция не удалась",
  job_log: "Показать полный журнал",
  open_chat: "Открыть чат", open_folder: "Открыть папку",
  res_summary: "{msgs} сообщений на {pages} страниц(ах)",
  res_range: "С {a} по {b} · ",
  res_own: " · {n} ваших сообщений",
  snack_dup: "Этот экспорт уже в списке",
  snack_need_output: "Укажите папку назначения",
  stage_scan: "Сканирование {name}…", stage_merge: "Объединение и дедупликация сообщений…",
  stage_media: "Копирование медиа · {copied} файлов…",
  stage_write: "Запись страниц…", stage_enhance: "Улучшение {name}…",
  stage_working: "Обработка…",
  pick_export: "Выберите папку экспорта Telegram",
  pick_output: "Выберите папку назначения",
  pick_compact: "Выберите экспорт для сжатия",
  pick_enhance: "Выберите экспорт для улучшения",
  feat_bubbles: "Пузыри и фон чата",
  feat_bubbles_d: "Настоящий вид Telegram с настраиваемым расположением",
  feat_quotes: "Цитаты ответов",
  feat_quotes_d: "Рамка с автором и фрагментом вместо \"In reply to this message\"",
  feat_theme: "Светлая / тёмная тема",
  feat_theme_d: "Плавающая кнопка для смены темы при просмотре чата",
  feat_media: "Живое медиа",
  feat_media_d: "Видео и аудио воспроизводятся на месте, фото в просмотрщике",
  feat_note: "Заключительное сообщение с инструкциями",
  feat_note_d: "Заметка в конце чата о том, как пользоваться этими инструментами",
  restore_btn: "Вернуть оригинал",
  job_restoring: "Восстановление оригинального дизайна…",
  stage_restore: "Восстановление {name}…",
  kind_group: "👥 Группа · {n} участников",
  kind_private: "👤 Личный чат",
  confirm_title: "Разные чаты?",
  confirm_mix: "«{a}» не совпадает с «{b}». Похоже, это экспорты разных чатов, и их объединение перемешает переписки. Всё равно добавить?",
  confirm_kind: "Вы смешиваете групповой чат с личным. Всё равно добавить?",
  btn_cancel: "Отмена",
  btn_continue: "Добавить всё равно",
  fuse_add_more: "Добавьте или перетащите сюда ещё одну папку экспорта",
  locating: "Поиск «{name}» на диске…",
  locate_fail: "Не удалось найти папку на диске. Добавьте её один раз кнопкой, и дальнейшие перетаскивания из этого места будут работать.",
  drop_only_folders: "Перетаскивайте папки экспорта, а не файлы",
  drop_no_messages: "«{name}» не содержит messages.html",
  shutdown_tooltip: "Выключить приложение",
  shutdown_title: "Выключить приложение?",
  shutdown_body: "Оно выключится, и эта вкладка перестанет работать, <strong><u>даже если что-то ещё выполняется</u></strong>.",
  shutdown_confirm: "Выключить",
  shutdown_done_h: "Приложение выключено",
  shutdown_done_p: "Теперь можно закрыть эту вкладку. Чтобы снова использовать приложение, откройте скрипт или исполняемый файл заново.",
  tab_convert: "Конвертер",
  convert_source: "Папка для конвертации",
  convert_pick: "Выберите или перетащите сюда папку экспорта<br>(HTML, JSON или оба — определяется автоматически)",
  convert_opts: "Параметры конвертации",
  convert_mode: "Режим конвертации в JSON",
  mode_enriched: "Расширенный (рекомендуется)",
  mode_faithful: "Официальный формат",
  mode_hint_enriched: "JSON по официальной схеме Telegram плюс дополнительные поля (тексты статуса звонков, имена файлов…), чтобы ничего из HTML не потерялось.",
  mode_hint_faithful: "Только ключи официального result.json из Telegram Desktop, без каких-либо дополнительных полей.",
  faithful_warn: "ДЕСТРУКТИВНЫЙ ПРОЦЕСС: официальный формат отбрасывает данные, которых в нём нет (направление и текст статуса звонков, имена файлов…), а также УДАЛЯЕТ страницы messages*.html и веб-ресурсы (css/, js/, images/) из папки, чтобы она соответствовала настоящему JSON-экспорту Telegram. Если эти данные или HTML нужны, используйте расширенный режим.",
  tojson_hint: "Результат сохраняется как result.json внутри самой папки экспорта — так относительные пути к медиа (photos/…, voice_messages/…) продолжают работать. Если result.json, созданный не этим инструментом, уже существует, операция останавливается, чтобы никогда не перезаписать официальный экспорт.",
  tohtml_hint: "Просматриваемый HTML-вид (messages*.html) будет создан в той же папке, включая веб-структуру (css/js/images), которой нет в JSON-экспорте. Существующие медиафайлы используются как есть, без копирования. Уже существующие страницы messages*.html никогда не перезаписываются без подтверждения.",
  enrich_hint: "Оба формата будут объединены: официальный result.json (содержащий данные, которых нет в HTML: id, даты правок, размеры…) будет дополнен полями, извлекаемыми из HTML. Записывается result_enriched.json, исходные файлы не изменяются.",
  eo_choice: "В этой папке есть только расширенный JSON. Что вы хотите сделать?",
  eo_hint_tohtml: "Страницы messages*.html и веб-ресурсы (css/js/images) будут созданы на основе этого расширенного JSON.",
  eo_hint_downgrade: "Поля и метка, добавленные при расширении, будут удалены — получится result.json, соответствующий официальному формату Telegram.",
  downgrade_warn: "ДЕСТРУКТИВНЫЙ ПРОЦЕСС: дополнительные данные, существовавшие только в расширенном JSON (направление и текст статуса звонков, имена файлов…), будут потеряны и не смогут быть восстановлены без исходного HTML.",
  convert_btn_downgrade: "Вернуть к официальному формату",
  res_removed_assets: " · страницы и ресурсы HTML удалены",
  det_html: "Обнаружено: HTML-экспорт → будет сконвертирован в JSON",
  det_json: "Обнаружено: JSON-экспорт → будет создан HTML-вид",
  det_enriched_only: "Обнаружено: только расширенный JSON → выберите, что делать",
  det_both: "Обнаружено: HTML + JSON одновременно → обогатить JSON",
  both_title: "⚠ Папка содержит ОБА формата",
  both_body: "Эта папка содержит ОДНОВРЕМЕННО HTML-экспорт (messages.html) и официальный JSON (result.json). Конвертация одного в другой ничего не даёт: у вас уже есть оба. Единственная полезная операция — ОБОГАТИТЬ официальный JSON дополнительными данными из HTML (записывается result_enriched.json, оригиналы не изменяются). Обогатить JSON или отменить операцию?",
  btn_enrich: "Обогатить JSON",
  convert_btn_tojson: "Конвертировать в JSON",
  convert_btn_tohtml: "Создать HTML-вид",
  convert_btn_enrich: "Обогатить JSON",
  job_converting: "Конвертация…",
  stage_convert: "Конвертация сообщений…",
  res_convert: "{msgs} сообщений",
  res_enriched: "{msgs} сообщений · {n} полей добавлено из HTML",
  pick_convert: "Выберите папку экспорта для конвертации",
  open_json: "Открыть JSON",
  footer: "Telegram Export Studio · работает полностью на вашем компьютере"
},
zh: {
  subtitle: "合并、压缩并美化聊天导出 — 100% 本地运行",
  tab_fuse: "合并", tab_compact: "压缩", tab_enhance: "美化",
  fuse_sources: "源导出",
  fuse_empty: "添加或拖入两个及以上的 Telegram 导出文件夹<br>将它们合并为一个",
  add_folder: "添加文件夹",
  dest_pagination: "目标与分页",
  dest_label: "目标文件夹",
  size_label: "页面大小",
  chip_single: "单个文件", chip_custom: "自定义",
  fuse_hint: "Telegram 使用约 500 KB 的页面。页面越大，messages.html 文件越少。",
  fuse_btn: "合并导出", fuse_btn_n: "合并 {n} 个导出",
  compact_source: "要压缩的导出",
  compact_pick: "选择或拖入导出文件夹<br>（Telegram 导出或合并结果均可）",
  goal: "目标", mode_files: "文件数量", mode_size: "每页大小",
  files_hint: "1 = 全部历史记录合并到一个 messages.html",
  goal_hint_files: "你决定最终要多少个 messages.html（1 = 全部合一），每页大小自动计算。",
  goal_hint_size: "你设定每个 messages.html 的最大体积，文件数量由历史记录总量决定。",
  srclink: "开源项目 · 在 GitHub 上查看",
  inplace_hint: "就地重新分页：只重写 messages*.html；照片、视频和音频不会被改动。",
  compact_btn: "压缩",
  enhance_source: "要美化的导出",
  enhance_pick: "选择或拖入导出文件夹<br>（原始、已合并或已压缩均可）",
  enhance_opts: "显示选项",
  me_label: "你是谁？（所选人的消息将以不同颜色高亮，标记为你的消息）",
  me_none: "— 仅原始设计，不区分己方 —",
  layout_label: "消息布局",
  layout_both: "两者（可切换）", layout_chat: "对话", layout_original: "原始",
  layout_hint_original: "所有头像在左侧，与原始 Telegram 导出一致。",
  layout_hint_chat: "头像分列两侧：对方在左，你在右。",
  layout_hint_both: "聊天中的悬浮按钮可在两种布局间切换。",
  feat_fullwidth: "全宽显示",
  feat_fullwidth_d: "去掉 Telegram 的居中栏：聊天动态占满整个屏幕",
  enhance_hint: "Telegram 风格气泡、回复引用、明暗主题、视频音频可播放、照片查看器。完全兼容：之后仍可继续合并和压缩此导出。",
  enhance_btn: "美化导出",
  need_me: "请选择你是谁以启用对话模式",
  already_enhanced: "此导出已经美化过；将会更新",
  item_sub: "{msgs} 条消息 · {pages} 页 · 媒体 {media}",
  item_more: "还有 {n} 个",
  job_fusing: "正在合并导出…", job_compacting: "正在压缩导出…",
  job_enhancing: "正在美化导出…",
  job_done: "完成", job_error: "错误",
  job_failed: "操作失败",
  job_log: "查看完整日志",
  open_chat: "打开聊天", open_folder: "打开文件夹",
  res_summary: "{msgs} 条消息，共 {pages} 页",
  res_range: "从 {a} 到 {b} · ",
  res_own: " · 你的消息 {n} 条",
  snack_dup: "该导出已在列表中",
  snack_need_output: "请指定目标文件夹",
  stage_scan: "正在扫描 {name}…", stage_merge: "正在合并与去重消息…",
  stage_media: "正在复制媒体 · {copied} 个文件…",
  stage_write: "正在写入页面…", stage_enhance: "正在美化 {name}…",
  stage_working: "处理中…",
  pick_export: "选择一个 Telegram 导出文件夹",
  pick_output: "选择目标文件夹",
  pick_compact: "选择要压缩的导出",
  pick_enhance: "选择要美化的导出",
  feat_bubbles: "气泡与聊天背景",
  feat_bubbles_d: "真实的 Telegram 外观，布局可配置",
  feat_quotes: "回复引用",
  feat_quotes_d: "显示作者和片段的引用框，替代 \"In reply to this message\"",
  feat_theme: "明亮 / 暗黑模式",
  feat_theme_d: "查看聊天时用悬浮按钮切换主题",
  feat_media: "实时媒体",
  feat_media_d: "视频和音频可直接播放，照片在查看器中打开",
  feat_note: "结尾说明消息",
  feat_note_d: "在聊天末尾附上如何使用这些工具的说明",
  restore_btn: "撤销美化",
  job_restoring: "正在恢复原始设计…",
  stage_restore: "正在恢复 {name}…",
  kind_group: "👥 群组 · {n} 位成员",
  kind_private: "👤 私聊",
  confirm_title: "不同的聊天？",
  confirm_mix: "「{a}」与「{b}」不匹配。这些导出似乎来自不同的聊天，合并会混淆对话。仍要添加吗？",
  confirm_kind: "你正在把群聊和私聊混在一起。仍要添加吗？",
  btn_cancel: "取消",
  btn_continue: "仍然添加",
  fuse_add_more: "在此添加或拖入另一个导出文件夹",
  locating: "正在磁盘上查找「{name}」…",
  locate_fail: "无法在磁盘上找到该文件夹。请先用按钮添加一次，之后从该位置拖入即可正常工作。",
  drop_only_folders: "请拖入导出文件夹，而不是文件",
  drop_no_messages: "「{name}」不包含 messages.html",
  shutdown_tooltip: "关闭应用",
  shutdown_title: "要关闭应用吗？",
  shutdown_body: "应用将关闭，此标签页将不再可用，<strong><u>即使有任务正在进行中也会关闭</u></strong>。",
  shutdown_confirm: "关闭",
  shutdown_done_h: "应用已关闭",
  shutdown_done_p: "现在可以关闭此标签页了。要再次使用应用，请重新打开脚本或可执行文件。",
  tab_convert: "转换器",
  convert_source: "要转换的文件夹",
  convert_pick: "选择或拖拽导出文件夹到这里<br>（HTML、JSON 或两者 — 自动检测）",
  convert_opts: "转换选项",
  convert_mode: "JSON 转换模式",
  mode_enriched: "增强版（推荐）",
  mode_faithful: "官方格式",
  mode_hint_enriched: "采用 Telegram 官方架构的 JSON，并附加额外字段（通话状态文本、文件名等），确保 HTML 中的内容不丢失。",
  mode_hint_faithful: "仅保留 Telegram Desktop 官方 result.json 的键，不含任何额外字段。",
  faithful_warn: "破坏性过程：官方格式会丢弃其未涵盖的数据（通话方向和状态文本、文件名等），并会删除文件夹中的 messages*.html 页面和网页资源（css/、js/、images/），使其与真正的 Telegram JSON 导出完全一致。如果需要保留这些数据或 HTML，请使用增强模式。",
  tojson_hint: "结果保存为导出文件夹内的 result.json — 这样媒体的相对路径（photos/…、voice_messages/…）才能继续有效。如果已存在非本工具生成的 result.json，操作将停止，绝不覆盖官方导出。",
  tohtml_hint: "将在同一文件夹中生成可浏览的 HTML 视图（messages*.html），包括 JSON 导出所没有的网页结构（css/js/images）。现有媒体文件按原样引用，不会复制。已存在的 messages*.html 页面绝不会在未经确认的情况下被覆盖。",
  enrich_hint: "两种格式将被合并：官方 result.json（包含 HTML 所没有的数据：id、编辑日期、大小等）将补充从 HTML 中可恢复的额外字段。写入 result_enriched.json，不改动任何原始文件。",
  eo_choice: "此文件夹中只有一个增强版 JSON。你想怎么做？",
  eo_hint_tohtml: "将根据这个增强版 JSON 生成 messages*.html 页面和网页资源（css/js/images）。",
  eo_hint_downgrade: "将移除增强模式添加的字段和标记，得到与 Telegram 官方格式一致的 result.json。",
  downgrade_warn: "破坏性过程：仅存在于增强版 JSON 中的额外数据（通话方向和状态文本、文件名等）将会丢失，且无法在没有原始 HTML 的情况下恢复。",
  convert_btn_downgrade: "降级为官方格式",
  res_removed_assets: " · 已移除 HTML 页面和资源",
  det_html: "检测到：HTML 导出 → 将转换为 JSON",
  det_json: "检测到：JSON 导出 → 将生成 HTML 视图",
  det_enriched_only: "检测到：仅有增强版 JSON → 请选择操作",
  det_both: "检测到：同时存在 HTML + JSON → 增强 JSON",
  both_title: "⚠ 文件夹同时包含两种格式",
  both_body: "此文件夹同时包含 HTML 导出（messages.html）和官方 JSON（result.json）。相互转换没有意义：两者你都已拥有。唯一有用的操作是用 HTML 的额外数据来增强官方 JSON（写入 result_enriched.json，原文件不变）。增强 JSON 还是取消操作？",
  btn_enrich: "增强 JSON",
  convert_btn_tojson: "转换为 JSON",
  convert_btn_tohtml: "生成 HTML 视图",
  convert_btn_enrich: "增强 JSON",
  job_converting: "转换中…",
  stage_convert: "正在转换消息…",
  res_convert: "{msgs} 条消息",
  res_enriched: "{msgs} 条消息 · 从 HTML 添加了 {n} 个字段",
  pick_convert: "选择要转换的导出文件夹",
  open_json: "打开 JSON",
  footer: "Telegram Export Studio · 完全在你的设备上运行"
},
ja: {
  subtitle: "チャットのエクスポートを結合・圧縮・強化 — 100% ローカル",
  tab_fuse: "結合", tab_compact: "圧縮", tab_enhance: "強化",
  fuse_sources: "元のエクスポート",
  fuse_empty: "Telegram のエクスポートフォルダを 2 つ以上<br>ここに追加またはドラッグして 1 つに結合します",
  add_folder: "フォルダを追加",
  dest_pagination: "保存先とページ分割",
  dest_label: "保存先フォルダ",
  size_label: "ページサイズ",
  chip_single: "単一ファイル", chip_custom: "カスタム",
  fuse_hint: "Telegram は約 500 KB のページを使います。ページが大きいほど messages.html の数は減ります。",
  fuse_btn: "エクスポートを結合", fuse_btn_n: "{n} 件のエクスポートを結合",
  compact_source: "圧縮するエクスポート",
  compact_pick: "エクスポートフォルダを選択またはドラッグ<br>（Telegram のエクスポートまたは結合結果）",
  goal: "目標", mode_files: "ファイル数", mode_size: "ページあたりのサイズ",
  files_hint: "1 = 全履歴を 1 つの messages.html に",
  goal_hint_files: "最終的に残す messages.html の数を指定します（1 = すべてを 1 つに）。ページサイズは自動計算されます。",
  goal_hint_size: "各 messages.html の最大サイズを指定します。ファイル数は履歴の総量で決まります。",
  srclink: "オープンソース · GitHub で見る",
  inplace_hint: "その場で再分割します。書き換えるのは messages*.html のみで、写真・動画・音声には触れません。",
  compact_btn: "圧縮",
  enhance_source: "強化するエクスポート",
  enhance_pick: "エクスポートフォルダを選択またはドラッグ<br>（オリジナル・結合済み・圧縮済みのいずれも可）",
  enhance_opts: "表示オプション",
  me_label: "あなたは誰ですか？（選んだ人のメッセージは別の色でハイライトされ、自分のものとして表示されます）",
  me_none: "— オリジナルデザインのみ、自分側なし —",
  layout_label: "メッセージのレイアウト",
  layout_both: "両方（切替可）", layout_chat: "会話", layout_original: "オリジナル",
  layout_hint_original: "元のエクスポートと同じく、すべてのアバターが左側に表示されます。",
  layout_hint_chat: "アバターが両側に：相手は左、あなたは右。",
  layout_hint_both: "チャット内のフローティングボタンで両方のレイアウトを切り替えられます。",
  feat_fullwidth: "全幅表示",
  feat_fullwidth_d: "Telegram の中央カラムをなくし、チャットが画面全体に広がります",
  enhance_hint: "Telegram 風の吹き出し、返信の引用、ライト/ダークテーマ、動画と音声の再生、写真ビューア。互換性あり：このエクスポートは後からでも結合・圧縮できます。",
  enhance_btn: "エクスポートを強化",
  need_me: "会話モードにはあなたが誰かを選んでください",
  already_enhanced: "このエクスポートは既に強化済みです。更新されます",
  item_sub: "{msgs} 件のメッセージ · {pages} ページ · メディア {media}",
  item_more: "他 {n} 件",
  job_fusing: "エクスポートを結合中…", job_compacting: "エクスポートを圧縮中…",
  job_enhancing: "エクスポートを強化中…",
  job_done: "完了", job_error: "エラー",
  job_failed: "操作に失敗しました",
  job_log: "完全なログを表示",
  open_chat: "チャットを開く", open_folder: "フォルダを開く",
  res_summary: "{msgs} 件のメッセージ、{pages} ページ",
  res_range: "{a} から {b} まで · ",
  res_own: " · あなたのメッセージ {n} 件",
  snack_dup: "そのエクスポートは既にリストにあります",
  snack_need_output: "保存先フォルダを指定してください",
  stage_scan: "{name} をスキャン中…", stage_merge: "メッセージを結合・重複排除中…",
  stage_media: "メディアをコピー中 · {copied} ファイル…",
  stage_write: "ページを書き込み中…", stage_enhance: "{name} を強化中…",
  stage_working: "処理中…",
  pick_export: "Telegram のエクスポートフォルダを選択",
  pick_output: "保存先フォルダを選択",
  pick_compact: "圧縮するエクスポートを選択",
  pick_enhance: "強化するエクスポートを選択",
  feat_bubbles: "吹き出しとチャット背景",
  feat_bubbles_d: "本物の Telegram の見た目、レイアウトは設定可能",
  feat_quotes: "返信の引用",
  feat_quotes_d: "\"In reply to this message\" の代わりに作者と抜粋付きの引用枠",
  feat_theme: "ライト / ダークモード",
  feat_theme_d: "チャット閲覧中にフローティングボタンでテーマを切替",
  feat_media: "ライブメディア",
  feat_media_d: "動画と音声をその場で再生、写真はビューアで表示",
  feat_note: "使い方の最終メッセージ",
  feat_note_d: "チャットの最後にこれらのツールの使い方を記載",
  restore_btn: "元に戻す",
  job_restoring: "オリジナルデザインを復元中…",
  stage_restore: "{name} を復元中…",
  kind_group: "👥 グループ · {n} 人",
  kind_private: "👤 個人チャット",
  confirm_title: "別のチャット？",
  confirm_mix: "「{a}」は「{b}」と一致しません。これらは別のチャットのエクスポートのようで、結合すると会話が混ざります。それでも追加しますか？",
  confirm_kind: "グループチャットと個人チャットを混ぜようとしています。それでも追加しますか？",
  btn_cancel: "キャンセル",
  btn_continue: "それでも追加",
  fuse_add_more: "別のエクスポートフォルダをここに追加またはドラッグ",
  locating: "ディスク上で「{name}」を検索中…",
  locate_fail: "ディスク上でフォルダが見つかりませんでした。一度ボタンで追加すると、その場所からのドラッグが使えるようになります。",
  drop_only_folders: "ファイルではなくエクスポートフォルダをドロップしてください",
  drop_no_messages: "「{name}」に messages.html がありません",
  shutdown_tooltip: "アプリを終了する",
  shutdown_title: "アプリを終了しますか？",
  shutdown_body: "<strong><u>処理中のものがあっても終了し</u></strong>、このタブは使用できなくなります。",
  shutdown_confirm: "終了する",
  shutdown_done_h: "アプリを終了しました",
  shutdown_done_p: "このタブは閉じて構いません。アプリを再度使うには、スクリプトまたは実行ファイルをもう一度開いてください。",
  tab_convert: "コンバーター",
  convert_source: "変換するフォルダー",
  convert_pick: "エクスポートフォルダーを選択またはここにドラッグ<br>（HTML・JSON・両方 — 自動検出）",
  convert_opts: "変換オプション",
  convert_mode: "JSON 変換モード",
  mode_enriched: "拡張版（推奨）",
  mode_faithful: "公式フォーマット",
  mode_hint_enriched: "Telegram の公式スキーマに追加フィールド（通話ステータスのテキスト、ファイル名など）を加えた JSON。HTML の内容を一切失いません。",
  mode_hint_faithful: "Telegram Desktop の公式 result.json のキーのみで、追加フィールドは一切ありません。",
  faithful_warn: "破壊的な処理：公式フォーマットでは、対応していないデータ（通話の方向やステータステキスト、ファイル名など）が失われるうえ、フォルダー内の messages*.html ページとウェブ資産（css/、js/、images/）が削除され、本物の Telegram JSON エクスポートと同じ構成になります。それらのデータや HTML が必要な場合は拡張モードを使用してください。",
  tojson_hint: "結果はエクスポートフォルダー内に result.json として保存されます — これによりメディアの相対パス（photos/…、voice_messages/…）が引き続き機能します。このツールが生成していない result.json が既にある場合、公式エクスポートを上書きしないよう処理は停止します。",
  tohtml_hint: "閲覧可能な HTML ビュー（messages*.html）が同じフォルダーに生成され、JSON エクスポートに含まれないウェブ構造（css/js/images）も追加されます。既存のメディアはコピーせずそのまま参照します。既存の messages*.html ページが確認なしに上書きされることはありません。",
  enrich_hint: "両方のフォーマットを統合します：公式の result.json（HTML にないデータ：id、編集日時、サイズなど）に、HTML から復元できる追加フィールドを補います。result_enriched.json が書き込まれ、元のファイルには触れません。",
  eo_choice: "このフォルダーには拡張版 JSON しかありません。何をしますか？",
  eo_hint_tohtml: "この拡張版 JSON から messages*.html ページとウェブ資産（css/js/images）を生成します。",
  eo_hint_downgrade: "拡張処理で追加されたフィールドとマークを削除し、Telegram の公式フォーマットと同じ result.json にします。",
  downgrade_warn: "破壊的な処理：拡張版 JSON にのみ存在していた追加データ（通話の方向やステータステキスト、ファイル名など）は失われ、元の HTML なしでは復元できません。",
  convert_btn_downgrade: "公式フォーマットに戻す",
  res_removed_assets: " · HTML ページと資産を削除しました",
  det_html: "検出：HTML エクスポート → JSON に変換します",
  det_json: "検出：JSON エクスポート → HTML ビューを生成します",
  det_enriched_only: "検出：拡張版 JSON のみ → 操作を選択してください",
  det_both: "検出：HTML + JSON が同時に存在 → JSON を拡張",
  both_title: "⚠ フォルダーに両方のフォーマットがあります",
  both_body: "このフォルダーには HTML エクスポート（messages.html）と公式 JSON（result.json）が同時に含まれています。相互変換は無意味です：すでに両方お持ちです。有用なのは、HTML の追加データで公式 JSON を拡張することだけです（result_enriched.json を書き込み、元ファイルは変更しません）。JSON を拡張しますか、それとも操作をキャンセルしますか？",
  btn_enrich: "JSON を拡張",
  convert_btn_tojson: "JSON に変換",
  convert_btn_tohtml: "HTML ビューを生成",
  convert_btn_enrich: "JSON を拡張",
  job_converting: "変換中…",
  stage_convert: "メッセージを変換中…",
  res_convert: "{msgs} 件のメッセージ",
  res_enriched: "{msgs} 件のメッセージ · HTML から {n} 個のフィールドを追加",
  pick_convert: "変換するエクスポートフォルダーを選択",
  open_json: "JSON を開く",
  footer: "Telegram Export Studio · すべてあなたの端末上で動作します"
},
hi: {
  subtitle: "चैट एक्सपोर्ट को मिलाएँ, संक्षिप्त करें और बेहतर बनाएँ — 100% लोकल",
  tab_fuse: "मिलाएँ", tab_compact: "संक्षिप्त करें", tab_enhance: "बेहतर बनाएँ",
  fuse_sources: "स्रोत एक्सपोर्ट",
  fuse_empty: "दो या अधिक Telegram एक्सपोर्ट फ़ोल्डर यहाँ जोड़ें<br>या खींचकर छोड़ें ताकि वे एक में मिल जाएँ",
  add_folder: "फ़ोल्डर जोड़ें",
  dest_pagination: "गंतव्य और पेज विभाजन",
  dest_label: "गंतव्य फ़ोल्डर",
  size_label: "पेज का आकार",
  chip_single: "एक ही फ़ाइल", chip_custom: "कस्टम",
  fuse_hint: "Telegram लगभग 500 KB के पेज इस्तेमाल करता है। बड़े पेज = कम messages.html फ़ाइलें।",
  fuse_btn: "एक्सपोर्ट मिलाएँ", fuse_btn_n: "{n} एक्सपोर्ट मिलाएँ",
  compact_source: "संक्षिप्त करने के लिए एक्सपोर्ट",
  compact_pick: "एक्सपोर्ट फ़ोल्डर चुनें या यहाँ खींचें<br>(Telegram एक्सपोर्ट या मिलाने का परिणाम)",
  goal: "लक्ष्य", mode_files: "फ़ाइलों की संख्या", mode_size: "प्रति पेज आकार",
  files_hint: "1 = पूरा इतिहास एक ही messages.html में",
  goal_hint_files: "आप तय करते हैं कि अंत में कितनी messages.html रहें (1 = सब एक में) और हर पेज का आकार अपने आप तय होता है।",
  goal_hint_size: "आप हर messages.html का अधिकतम आकार तय करते हैं और फ़ाइलों की संख्या पूरे इतिहास पर निर्भर करती है।",
  srclink: "ओपन सोर्स · GitHub पर देखें",
  inplace_hint: "वहीं पर पुनर्विभाजन होता है: केवल messages*.html दोबारा लिखी जाती हैं; फ़ोटो, वीडियो और ऑडियो अछूते रहते हैं।",
  compact_btn: "संक्षिप्त करें",
  enhance_source: "बेहतर बनाने के लिए एक्सपोर्ट",
  enhance_pick: "एक्सपोर्ट फ़ोल्डर चुनें या यहाँ खींचें<br>(मूल, मिला हुआ या संक्षिप्त)",
  enhance_opts: "देखने के विकल्प",
  me_label: "आप कौन हैं? (चुने गए व्यक्ति के संदेश अलग रंग में हाइलाइट होंगे ताकि पता चले कि वे आपके हैं)",
  me_none: "— केवल मूल डिज़ाइन, अपना पक्ष नहीं —",
  layout_label: "संदेशों का लेआउट",
  layout_both: "दोनों (बदल सकते हैं)", layout_chat: "बातचीत", layout_original: "मूल",
  layout_hint_original: "सभी अवतार बाईं ओर, मूल Telegram एक्सपोर्ट की तरह।",
  layout_hint_chat: "अवतार दोनों ओर: सामने वाला बाईं ओर, आप दाईं ओर।",
  layout_hint_both: "चैट में एक फ़्लोटिंग बटन दोनों लेआउट के बीच बदलता है।",
  feat_fullwidth: "पूरी चौड़ाई",
  feat_fullwidth_d: "Telegram का बीच वाला कॉलम हटाता है: चैट पूरी स्क्रीन घेरती है",
  enhance_hint: "Telegram जैसे बबल, जवाब के उद्धरण, लाइट/डार्क थीम, चलने वाले वीडियो-ऑडियो और फ़ोटो व्यूअर। संगत: बाद में भी इस एक्सपोर्ट को मिलाना और संक्षिप्त करना संभव रहेगा।",
  enhance_btn: "एक्सपोर्ट बेहतर बनाएँ",
  need_me: "बातचीत मोड के लिए चुनें कि आप कौन हैं",
  already_enhanced: "यह एक्सपोर्ट पहले से बेहतर बनाया गया था; इसे अपडेट किया जाएगा",
  item_sub: "{msgs} संदेश · {pages} पेज · मीडिया {media}",
  item_more: "+{n} और",
  job_fusing: "एक्सपोर्ट मिलाए जा रहे हैं…", job_compacting: "एक्सपोर्ट संक्षिप्त हो रहा है…",
  job_enhancing: "एक्सपोर्ट बेहतर बनाया जा रहा है…",
  job_done: "पूरा हुआ", job_error: "त्रुटि",
  job_failed: "कार्रवाई विफल रही",
  job_log: "पूरा लॉग देखें",
  open_chat: "चैट खोलें", open_folder: "फ़ोल्डर खोलें",
  res_summary: "{pages} पेज में {msgs} संदेश",
  res_range: "{a} से {b} तक · ",
  res_own: " · आपके {n} संदेश",
  snack_dup: "वह एक्सपोर्ट पहले से सूची में है",
  snack_need_output: "गंतव्य फ़ोल्डर बताएं",
  stage_scan: "{name} स्कैन हो रहा है…", stage_merge: "संदेश मिलाए और दोहराव हटाए जा रहे हैं…",
  stage_media: "मीडिया कॉपी हो रहा है · {copied} फ़ाइलें…",
  stage_write: "पेज लिखे जा रहे हैं…", stage_enhance: "{name} बेहतर बनाया जा रहा है…",
  stage_working: "प्रोसेस हो रहा है…",
  pick_export: "एक Telegram एक्सपोर्ट फ़ोल्डर चुनें",
  pick_output: "गंतव्य फ़ोल्डर चुनें",
  pick_compact: "संक्षिप्त करने के लिए एक्सपोर्ट चुनें",
  pick_enhance: "बेहतर बनाने के लिए एक्सपोर्ट चुनें",
  feat_bubbles: "बबल और चैट बैकग्राउंड",
  feat_bubbles_d: "असली Telegram जैसा रूप, लेआउट बदला जा सकता है",
  feat_quotes: "जवाब के उद्धरण",
  feat_quotes_d: "\"In reply to this message\" की जगह लेखक और अंश वाला बॉक्स",
  feat_theme: "लाइट / डार्क मोड",
  feat_theme_d: "चैट देखते समय थीम बदलने के लिए फ़्लोटिंग बटन",
  feat_media: "लाइव मीडिया",
  feat_media_d: "वीडियो और ऑडियो वहीं चलते हैं, फ़ोटो व्यूअर में खुलती हैं",
  feat_note: "निर्देशों वाला अंतिम संदेश",
  feat_note_d: "चैट के अंत में इन टूल्स के इस्तेमाल की जानकारी",
  restore_btn: "मूल रूप लौटाएँ",
  job_restoring: "मूल डिज़ाइन बहाल हो रहा है…",
  stage_restore: "{name} बहाल हो रहा है…",
  kind_group: "👥 समूह · {n} सदस्य",
  kind_private: "👤 निजी चैट",
  confirm_title: "अलग-अलग चैट?",
  confirm_mix: "«{a}» का «{b}» से मेल नहीं है। ये एक्सपोर्ट अलग-अलग चैट के लगते हैं और मिलाने से बातचीत गड़बड़ हो जाएगी। फिर भी जोड़ें?",
  confirm_kind: "आप एक समूह चैट को निजी चैट के साथ मिला रहे हैं। फिर भी जोड़ें?",
  btn_cancel: "रद्द करें",
  btn_continue: "फिर भी जोड़ें",
  fuse_add_more: "यहाँ एक और एक्सपोर्ट फ़ोल्डर जोड़ें या खींचें",
  locating: "डिस्क पर «{name}» खोजा जा रहा है…",
  locate_fail: "डिस्क पर फ़ोल्डर नहीं मिला। इसे एक बार बटन से जोड़ें, फिर उसी जगह से खींचना काम करेगा।",
  drop_only_folders: "फ़ाइलें नहीं, एक्सपोर्ट फ़ोल्डर छोड़ें",
  drop_no_messages: "«{name}» में messages.html नहीं है",
  shutdown_tooltip: "ऐप बंद करें",
  shutdown_title: "ऐप बंद करें?",
  shutdown_body: "यह बंद हो जाएगा और यह टैब काम करना बंद कर देगा, <strong><u>भले ही कोई काम अभी चल रहा हो</u></strong>।",
  shutdown_confirm: "बंद करें",
  shutdown_done_h: "ऐप बंद हो गया",
  shutdown_done_p: "अब आप यह टैब बंद कर सकते हैं। ऐप फिर से इस्तेमाल करने के लिए, स्क्रिप्ट या एक्ज़ीक्यूटेबल फिर से खोलें।",
  tab_convert: "कन्वर्टर",
  convert_source: "कन्वर्ट करने के लिए फ़ोल्डर",
  convert_pick: "एक्सपोर्ट फ़ोल्डर चुनें या यहाँ खींचें<br>(HTML, JSON या दोनों — अपने आप पहचाना जाता है)",
  convert_opts: "कन्वर्ज़न विकल्प",
  convert_mode: "JSON कन्वर्ज़न मोड",
  mode_enriched: "समृद्ध (अनुशंसित)",
  mode_faithful: "आधिकारिक फ़ॉर्मैट",
  mode_hint_enriched: "Telegram की आधिकारिक स्कीमा वाला JSON, साथ में अतिरिक्त फ़ील्ड (कॉल स्थिति के टेक्स्ट, फ़ाइल नाम…) ताकि HTML की कोई जानकारी न खोए।",
  mode_hint_faithful: "केवल Telegram Desktop की आधिकारिक result.json की कुंजियाँ, बिना किसी अतिरिक्त फ़ील्ड के।",
  faithful_warn: "विनाशकारी प्रक्रिया: आधिकारिक फ़ॉर्मैट वह डेटा हटा देता है जो उसमें शामिल नहीं है (कॉल की दिशा और स्थिति के टेक्स्ट, फ़ाइल नाम…) और फ़ोल्डर से messages*.html पेज तथा वेब एसेट्स (css/, js/, images/) भी मिटा देता है, ताकि वह असली Telegram JSON एक्सपोर्ट जैसा ही बन जाए। यदि यह डेटा या HTML चाहिए, तो समृद्ध मोड इस्तेमाल करें।",
  tojson_hint: "परिणाम एक्सपोर्ट फ़ोल्डर के अंदर ही result.json के रूप में सहेजा जाता है — ताकि मीडिया के सापेक्ष पथ (photos/…, voice_messages/…) काम करते रहें। यदि पहले से कोई ऐसा result.json मौजूद है जो इस टूल ने नहीं बनाया, तो आधिकारिक एक्सपोर्ट को कभी न मिटाने के लिए ऑपरेशन रुक जाता है।",
  tohtml_hint: "ब्राउज़ करने योग्य HTML व्यू (messages*.html) उसी फ़ोल्डर में बनाया जाएगा, जिसमें वह वेब संरचना (css/js/images) भी होगी जो JSON एक्सपोर्ट में नहीं आती। मौजूदा मीडिया जस का तस संदर्भित होता है, कभी कॉपी नहीं होता। पहले से मौजूद messages*.html पेज बिना पुष्टि के कभी नहीं मिटाए जाते।",
  enrich_hint: "दोनों फ़ॉर्मैट मिलाए जाएँगे: आधिकारिक result.json (जिसमें HTML में न होने वाला डेटा है: id, संपादन तिथियाँ, आकार…) को HTML से पुनर्प्राप्त होने वाले अतिरिक्त फ़ील्ड से समृद्ध किया जाएगा। result_enriched.json लिखा जाता है, मूल फ़ाइलें अछूती रहती हैं।",
  eo_choice: "इस फ़ोल्डर में केवल एक समृद्ध JSON है। आप क्या करना चाहते हैं?",
  eo_hint_tohtml: "इस समृद्ध JSON से messages*.html पेज और वेब एसेट्स (css/js/images) बनाए जाएँगे।",
  eo_hint_downgrade: "समृद्धिकरण द्वारा जोड़े गए फ़ील्ड और मार्क हटा दिए जाएँगे, जिससे Telegram के आधिकारिक फ़ॉर्मैट जैसा result.json बनेगा।",
  downgrade_warn: "विनाशकारी प्रक्रिया: केवल समृद्ध JSON में मौजूद अतिरिक्त डेटा (कॉल की दिशा और स्थिति के टेक्स्ट, फ़ाइल नाम…) खो जाएगा और मूल HTML के बिना वापस नहीं पाया जा सकेगा।",
  convert_btn_downgrade: "आधिकारिक फ़ॉर्मैट पर वापस जाएँ",
  res_removed_assets: " · HTML पेज और एसेट्स हटा दिए गए",
  det_html: "पहचाना गया: HTML एक्सपोर्ट → JSON में कन्वर्ट होगा",
  det_json: "पहचाना गया: JSON एक्सपोर्ट → HTML व्यू बनाया जाएगा",
  det_enriched_only: "पहचाना गया: केवल समृद्ध JSON → चुनें कि क्या करना है",
  det_both: "पहचाना गया: HTML + JSON एक साथ → JSON को समृद्ध करें",
  both_title: "⚠ फ़ोल्डर में दोनों फ़ॉर्मैट हैं",
  both_body: "इस फ़ोल्डर में एक साथ HTML एक्सपोर्ट (messages.html) और आधिकारिक JSON (result.json) दोनों हैं। एक को दूसरे में बदलने से कुछ नहीं मिलेगा: दोनों पहले से मौजूद हैं। एकमात्र उपयोगी काम है आधिकारिक JSON को HTML के अतिरिक्त डेटा से समृद्ध करना (result_enriched.json लिखा जाता है, मूल फ़ाइलें अछूती)। JSON समृद्ध करें या ऑपरेशन रद्द करें?",
  btn_enrich: "JSON समृद्ध करें",
  convert_btn_tojson: "JSON में कन्वर्ट करें",
  convert_btn_tohtml: "HTML व्यू बनाएँ",
  convert_btn_enrich: "JSON समृद्ध करें",
  job_converting: "कन्वर्ट हो रहा है…",
  stage_convert: "संदेश कन्वर्ट हो रहे हैं…",
  res_convert: "{msgs} संदेश",
  res_enriched: "{msgs} संदेश · HTML से {n} फ़ील्ड जोड़े गए",
  pick_convert: "कन्वर्ट करने के लिए एक्सपोर्ट फ़ोल्डर चुनें",
  open_json: "JSON खोलें",
  footer: "Telegram Export Studio · पूरी तरह आपके कंप्यूटर पर चलता है"
},
ar: {
  subtitle: "ادمج وضغّط وحسّن تصديرات المحادثات — 100% محليًا",
  tab_fuse: "دمج", tab_compact: "ضغط", tab_enhance: "تحسين",
  fuse_sources: "التصديرات المصدر",
  fuse_empty: "أضف أو اسحب هنا مجلدين أو أكثر من تصديرات تيليجرام<br>لدمجها في تصدير واحد",
  add_folder: "إضافة مجلد",
  dest_pagination: "الوجهة وتقسيم الصفحات",
  dest_label: "مجلد الوجهة",
  size_label: "حجم الصفحة",
  chip_single: "ملف واحد", chip_custom: "مخصص",
  fuse_hint: "يستخدم تيليجرام صفحات بحجم ~500 كيلوبايت. صفحات أكبر = ملفات messages.html أقل.",
  fuse_btn: "دمج التصديرات", fuse_btn_n: "دمج {n} تصديرات",
  compact_source: "التصدير المراد ضغطه",
  compact_pick: "اختر أو اسحب هنا مجلد التصدير<br>(تصدير تيليجرام أو نتيجة دمج)",
  goal: "الهدف", mode_files: "عدد الملفات", mode_size: "حجم كل صفحة",
  files_hint: "1 = كل السجل في ملف messages.html واحد",
  goal_hint_files: "تختار كم ملف messages.html تريد في النهاية (1 = الكل في ملف واحد) ويُحسب حجم كل صفحة تلقائيًا.",
  goal_hint_size: "تحدد الحجم الأقصى لكل messages.html ويتحدد عدد الملفات حسب إجمالي السجل.",
  srclink: "مفتوح المصدر · شاهده على GitHub",
  inplace_hint: "تتم إعادة التقسيم في المكان نفسه: تُعاد كتابة ملفات messages*.html فقط؛ ولا تُمَس الصور والفيديو والصوت.",
  compact_btn: "ضغط",
  enhance_source: "التصدير المراد تحسينه",
  enhance_pick: "اختر أو اسحب هنا مجلد التصدير<br>(أصلي أو مدموج أو مضغوط)",
  enhance_opts: "خيارات العرض",
  me_label: "من أنت؟ (ستُميَّز رسائل الشخص المختار بلون مختلف للإشارة إلى أنها رسائلك)",
  me_none: "— التصميم الأصلي فقط، بدون جانب خاص —",
  layout_label: "تخطيط الرسائل",
  layout_both: "كلاهما (قابل للتبديل)", layout_chat: "محادثة", layout_original: "أصلي",
  layout_hint_original: "كل الصور الرمزية على اليسار، كما في تصدير تيليجرام الأصلي.",
  layout_hint_chat: "الصور الرمزية على الجانبين: محدِّثك في جهة وأنت في الأخرى.",
  layout_hint_both: "زر عائم في المحادثة يبدّل بين التخطيطين.",
  feat_fullwidth: "عرض كامل",
  feat_fullwidth_d: "يزيل عمود تيليجرام المركزي: تشغل المحادثة الشاشة كاملة",
  enhance_hint: "فقاعات بأسلوب تيليجرام، اقتباسات الردود، سمة فاتحة/داكنة، تشغيل الفيديو والصوت مباشرة وعارض للصور. متوافق: يمكنك دمج هذا التصدير وضغطه لاحقًا.",
  enhance_btn: "تحسين التصدير",
  need_me: "اختر من أنت لوضع المحادثة",
  already_enhanced: "هذا التصدير محسَّن مسبقًا؛ سيتم تحديثه",
  item_sub: "{msgs} رسالة · {pages} صفحة · وسائط {media}",
  item_more: "+{n} أخرى",
  job_fusing: "جارٍ دمج التصديرات…", job_compacting: "جارٍ ضغط التصدير…",
  job_enhancing: "جارٍ تحسين التصدير…",
  job_done: "اكتمل", job_error: "خطأ",
  job_failed: "فشلت العملية",
  job_log: "عرض السجل الكامل",
  open_chat: "فتح المحادثة", open_folder: "فتح المجلد",
  res_summary: "{msgs} رسالة في {pages} صفحة",
  res_range: "من {a} إلى {b} · ",
  res_own: " · {n} من رسائلك",
  snack_dup: "هذا التصدير موجود في القائمة بالفعل",
  snack_need_output: "حدد مجلد الوجهة",
  stage_scan: "جارٍ فحص {name}…", stage_merge: "جارٍ دمج الرسائل وإزالة التكرار…",
  stage_media: "جارٍ نسخ الوسائط · {copied} ملفًا…",
  stage_write: "جارٍ كتابة الصفحات…", stage_enhance: "جارٍ تحسين {name}…",
  stage_working: "جارٍ المعالجة…",
  pick_export: "اختر مجلد تصدير تيليجرام",
  pick_output: "اختر مجلد الوجهة",
  pick_compact: "اختر التصدير المراد ضغطه",
  pick_enhance: "اختر التصدير المراد تحسينه",
  feat_bubbles: "الفقاعات وخلفية المحادثة",
  feat_bubbles_d: "مظهر تيليجرام الحقيقي مع تخطيط قابل للتخصيص",
  feat_quotes: "اقتباسات الردود",
  feat_quotes_d: "إطار يعرض الكاتب ومقتطفًا بدل \"In reply to this message\"",
  feat_theme: "الوضع الفاتح / الداكن",
  feat_theme_d: "زر عائم لتغيير السمة أثناء عرض المحادثة",
  feat_media: "وسائط مباشرة",
  feat_media_d: "تشغيل الفيديو والصوت في المكان، والصور في عارض",
  feat_note: "رسالة ختامية مع التعليمات",
  feat_note_d: "ملاحظة في نهاية المحادثة عن كيفية استخدام هذه الأدوات",
  restore_btn: "إزالة التحسين",
  job_restoring: "جارٍ استعادة التصميم الأصلي…",
  stage_restore: "جارٍ استعادة {name}…",
  kind_group: "👥 مجموعة · {n} مشاركًا",
  kind_private: "👤 محادثة خاصة",
  confirm_title: "محادثات مختلفة؟",
  confirm_mix: "«{a}» لا يطابق «{b}». يبدو أن هذه التصديرات من محادثات مختلفة ودمجها سيخلط المحادثات. أتريد إضافته على أي حال؟",
  confirm_kind: "أنت تخلط محادثة جماعية بمحادثة خاصة. أتريد الإضافة على أي حال؟",
  btn_cancel: "إلغاء",
  btn_continue: "إضافة على أي حال",
  fuse_add_more: "أضف أو اسحب هنا مجلد تصدير آخر",
  locating: "جارٍ البحث عن «{name}» في القرص…",
  locate_fail: "تعذر العثور على المجلد في القرص. أضفه مرة واحدة بالزر وستعمل عمليات السحب التالية من ذلك الموقع.",
  drop_only_folders: "اسحب مجلدات تصدير، لا ملفات",
  drop_no_messages: "«{name}» لا يحتوي على messages.html",
  shutdown_tooltip: "إيقاف تشغيل التطبيق",
  shutdown_title: "إيقاف تشغيل التطبيق؟",
  shutdown_body: "سيتوقف التطبيق ولن يعمل هذا التبويب بعد الآن، <strong><u>حتى لو كانت هناك عملية جارية</u></strong>.",
  shutdown_confirm: "إيقاف التشغيل",
  shutdown_done_h: "تم إيقاف تشغيل التطبيق",
  shutdown_done_p: "يمكنك الآن إغلاق هذا التبويب. لاستخدام التطبيق مرة أخرى، أعد فتح السكربت أو الملف التنفيذي.",
  tab_convert: "المحوِّل",
  convert_source: "المجلد المراد تحويله",
  convert_pick: "اختر أو اسحب مجلد التصدير هنا<br>(HTML أو JSON أو كلاهما — يُكتشف تلقائيًا)",
  convert_opts: "خيارات التحويل",
  convert_mode: "وضع التحويل إلى JSON",
  mode_enriched: "مُثرى (موصى به)",
  mode_faithful: "التنسيق الرسمي",
  mode_hint_enriched: "ملف JSON بالمخطط الرسمي لتيليجرام مع حقول إضافية (نصوص حالة المكالمات، أسماء الملفات…) حتى لا يضيع أي شيء مما يحتويه HTML.",
  mode_hint_faithful: "فقط مفاتيح result.json الرسمي من Telegram Desktop، دون أي حقول إضافية.",
  faithful_warn: "عملية تدميرية: التنسيق الرسمي يتخلص من البيانات التي لا يشملها (اتجاه المكالمات ونص حالتها، أسماء الملفات…) ويحذف صفحات messages*.html وموارد الويب (css/، js/، images/) من المجلد، بحيث يصبح مطابقًا لتصدير JSON حقيقي من تيليجرام. إذا كنت بحاجة إلى تلك البيانات أو إلى HTML فاستخدم الوضع المُثرى.",
  tojson_hint: "يُحفظ الناتج باسم result.json داخل مجلد التصدير نفسه — وهكذا تبقى المسارات النسبية للوسائط (photos/…، voice_messages/…) صالحة. إذا وُجد مسبقًا result.json لم تنشئه هذه الأداة، تتوقف العملية حتى لا يُستبدل التصدير الرسمي أبدًا.",
  tohtml_hint: "سيتم إنشاء واجهة HTML قابلة للتصفح (messages*.html) في نفس المجلد، مع البنية الويب (css/js/images) التي لا يتضمنها تصدير JSON. تُستخدم الوسائط الموجودة كما هي دون نسخها. صفحات messages*.html الموجودة مسبقًا لا تُستبدل أبدًا دون تأكيد.",
  enrich_hint: "سيتم دمج التنسيقين: ملف result.json الرسمي (الذي يحتوي بيانات لا يملكها HTML: المعرّفات، تواريخ التعديل، الأحجام…) سيُثرى بالحقول الإضافية القابلة للاستخراج من HTML. يُكتب result_enriched.json دون المساس بأي من الملفين الأصليين.",
  eo_choice: "هذا المجلد يحتوي فقط على JSON مُثرى. ماذا تريد أن تفعل؟",
  eo_hint_tohtml: "سيتم إنشاء صفحات messages*.html وموارد الويب (css/js/images) انطلاقًا من ملف JSON المُثرى هذا.",
  eo_hint_downgrade: "ستُحذف الحقول والعلامة التي أضافها الإثراء، لينتج ملف result.json مطابق للتنسيق الرسمي لتيليجرام.",
  downgrade_warn: "عملية تدميرية: البيانات الإضافية التي كانت موجودة فقط في JSON المُثرى (اتجاه المكالمات ونص حالتها، أسماء الملفات…) ستُفقد ولن يمكن استرجاعها دون ملف HTML الأصلي.",
  convert_btn_downgrade: "الرجوع إلى التنسيق الرسمي",
  res_removed_assets: " · تم حذف صفحات وموارد HTML",
  det_html: "اكتُشف: تصدير HTML → سيُحوَّل إلى JSON",
  det_json: "اكتُشف: تصدير JSON → ستُنشأ واجهة HTML",
  det_enriched_only: "اكتُشف: JSON مُثرى فقط → اختر ما تريد فعله",
  det_both: "اكتُشف: HTML + JSON معًا → إثراء JSON",
  both_title: "⚠ المجلد يحتوي على كلا التنسيقين",
  both_body: "يحتوي هذا المجلد في آنٍ واحد على تصدير HTML (ملف messages.html) وJSON الرسمي (ملف result.json). التحويل من تنسيق إلى الآخر لا يضيف شيئًا: كلاهما لديك بالفعل. العملية المفيدة الوحيدة هي إثراء JSON الرسمي بالبيانات الإضافية من HTML (يُكتب result_enriched.json دون المساس بالأصلين). أتريد إثراء JSON أم إلغاء العملية؟",
  btn_enrich: "إثراء JSON",
  convert_btn_tojson: "التحويل إلى JSON",
  convert_btn_tohtml: "إنشاء واجهة HTML",
  convert_btn_enrich: "إثراء JSON",
  job_converting: "جارٍ التحويل…",
  stage_convert: "جارٍ تحويل الرسائل…",
  res_convert: "{msgs} رسالة",
  res_enriched: "{msgs} رسالة · أُضيف {n} حقلًا من HTML",
  pick_convert: "اختر مجلد التصدير المراد تحويله",
  open_json: "فتح JSON",
  footer: "Telegram Export Studio · يعمل بالكامل على جهازك"
}
};

let LANG = localStorage.getItem("tgstudio-lang");
if (!I18N[LANG]) {
  const nav = (navigator.language || "en").slice(0, 2).toLowerCase();
  LANG = I18N[nav] ? nav : "en";
}

function t(key, vars) {
  let s = (I18N[LANG] && I18N[LANG][key]) || I18N.en[key] || key;
  if (vars) for (const k in vars) s = s.split("{" + k + "}").join(vars[k]);
  return s;
}

function applyLang() {
  document.documentElement.lang = LANG;
  document.documentElement.dir = LANG === "ar" ? "rtl" : "ltr";
  document.querySelectorAll("[data-i18n]").forEach(el => {
    el.textContent = t(el.dataset.i18n);
  });
  document.querySelectorAll("[data-i18n-html]").forEach(el => {
    el.innerHTML = t(el.dataset.i18nHtml);
  });
  $("lang").value = LANG;
  $("shutdown-btn").title = t("shutdown_tooltip");
  updateGoalHint();
  updateLayoutHint();
  renderExports();
  renderMeOptions();
  if (state.compact) renderCompactInfo();
  requestAnimationFrame(movePill);
}
$("lang").onchange = () => {
  LANG = $("lang").value;
  localStorage.setItem("tgstudio-lang", LANG);
  applyLang();
};

/* =============== helpers =============== */
async function api(path, body) {
  const r = await fetch(path, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {})
  });
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || "Error");
  return data;
}

function snack(msg) {
  const s = $("snack");
  s.textContent = msg;
  s.classList.add("show");
  clearTimeout(s._t);
  s._t = setTimeout(() => s.classList.remove("show"), 3600);
}

function confirmDialog(title, body, yesLabel, danger, bodyHtml) {
  return new Promise(resolve => {
    $("modal-title").textContent = title;
    if (bodyHtml) $("modal-body").innerHTML = body;
    else $("modal-body").textContent = body;
    $("modal-no").textContent = t("btn_cancel");
    $("modal-yes").textContent = yesLabel || t("btn_continue");
    $("modal-yes").classList.toggle("danger", !!danger);
    $("modal-back").classList.add("show");
    const close = ok => {
      $("modal-back").classList.remove("show");
      resolve(ok);
    };
    $("modal-no").onclick = () => close(false);
    $("modal-yes").onclick = () => close(true);
    $("modal-back").onclick = e => {
      if (e.target === $("modal-back")) close(false);
    };
  });
}

/* =============== cerrar el servidor =============== */
async function confirmShutdown() {
  const ok = await confirmDialog(
    t("shutdown_title"), t("shutdown_body"), t("shutdown_confirm"), true, true);
  if (!ok) return;
  $("shutdown-btn").classList.add("closed");
  try { await api("/api/shutdown"); } catch (e) { /* el servidor ya está cerrando */ }
  $("shutdown-screen").classList.add("show");
}

/* =============== verbose (solo escritorio/AIO) =============== */
let VERBOSE = localStorage.getItem("tgstudio-verbose") === "1";
function applyVerboseBtn() {
  $("verbose-btn").classList.toggle("active", VERBOSE);
}
async function toggleVerbose() {
  VERBOSE = !VERBOSE;
  localStorage.setItem("tgstudio-verbose", VERBOSE ? "1" : "0");
  applyVerboseBtn();
  try { await api("/api/verbose", { on: VERBOSE }); } catch (e) { /* no crítico */ }
  snack(VERBOSE ? "Verbose activado: detalles técnicos en el log del trabajo"
                : "Verbose desactivado");
}

/* =============== tabs =============== */
function movePill() {
  const btn = document.querySelector("#tabs button.active");
  const pill = $("pill");
  pill.style.left = btn.offsetLeft + "px";
  pill.style.width = btn.offsetWidth + "px";
}
document.querySelectorAll("#tabs button").forEach(b => {
  b.onclick = () => {
    document.querySelectorAll("#tabs button").forEach(x => x.classList.remove("active"));
    b.classList.add("active");
    state.view = b.dataset.view;
    ["fuse", "compact", "enhance", "convert"].forEach(v => {
      $("view-" + v).style.display = v === state.view ? "" : "none";
    });
    movePill();
  };
});
window.addEventListener("resize", movePill);

/* =============== chips =============== */
function wireChips(chipsId, customId, onSel) {
  $(chipsId).querySelectorAll(".chip").forEach(c => {
    c.onclick = () => {
      $(chipsId).querySelectorAll(".chip").forEach(x => x.classList.remove("sel"));
      c.classList.add("sel");
      if (customId) $(customId).classList.toggle("show", c.dataset.size === "custom");
      if (onSel) onSel();
    };
  });
}
wireChips("fuse-chips", "fuse-custom");
wireChips("compact-chips", "compact-custom");
wireChips("layout-chips", null, updateLayoutHint);

/* explicaciones dinamicas: cambian con la opcion elegida */
function updateGoalHint() {
  const mode = document.querySelector("#compact-mode button.active").dataset.mode;
  $("goal-hint").textContent = t(mode === "files" ? "goal_hint_files" : "goal_hint_size");
}
function updateLayoutHint() {
  const l = $("layout-chips").querySelector(".chip.sel").dataset.layout;
  $("layout-hint").textContent = t("layout_hint_" + l);
}

function chipValue(chipsId, nId, uId) {
  const sel = $(chipsId).querySelector(".chip.sel");
  if (sel.dataset.size !== "custom") return sel.dataset.size;
  return ($(nId).value || "1") + $(uId).value;
}

/* =============== fuse =============== */
function kindLabel(e) {
  return e.kind === "group"
    ? t("kind_group", { n: e.senders.length })
    : t("kind_private");
}

function itemSub(e) {
  return kindLabel(e) + " · " + t("item_sub",
    { msgs: e.messages, pages: e.pages.length, media: e.media_size })
    + (e.first ? " · " + e.first + " → " + e.last : "");
}

function renderExports() {
  const list = $("export-list");
  list.innerHTML = "";
  state.exports.forEach((e, i) => {
    const div = document.createElement("div");
    div.className = "export-item";
    div.innerHTML = `
      <div class="ficon"><svg viewBox="0 0 24 24"><path d="M10 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-8l-2-2z"/></svg></div>
      <div class="info"><b></b><span></span></div>
      <button class="iconbtn">
        <svg viewBox="0 0 24 24"><path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/></svg>
      </button>`;
    div.querySelector("b").textContent = e.name + (e.title ? " — " + e.title : "");
    div.querySelector("b").title = e.path;
    div.querySelector("span").textContent = itemSub(e);
    div.querySelector(".iconbtn").onclick = () => {
      state.exports.splice(i, 1);
      renderExports();
    };
    list.appendChild(div);
  });
  const n = state.exports.length;
  $("export-empty").classList.toggle("mini", n > 0);
  $("export-empty-text").innerHTML = t(n ? "fuse_add_more" : "fuse_empty");
  $("fuse-btn").disabled = n < 2;
  $("fuse-btn-label").textContent =
    n < 2 ? t("fuse_btn") : t("fuse_btn_n", { n });
}

async function addExportByPath(path) {
  if (state.exports.some(e => e.path === path)) return snack(t("snack_dup"));
  const info = await api("/api/inspect", { path });
  // safety: warn when the export looks like a different chat
  if (state.exports.length) {
    const first = state.exports[0];
    if (first.title && info.title && first.title !== info.title) {
      const ok = await confirmDialog(t("confirm_title"),
        t("confirm_mix", { a: info.title, b: first.title }));
      if (!ok) return;
      state.forceMix = true;
    } else if (first.kind !== info.kind) {
      const ok = await confirmDialog(t("confirm_title"), t("confirm_kind"));
      if (!ok) return;
      state.forceMix = true;
    }
  }
  state.exports.push(info);
  if (!$("output").value) {
    const sep = path.includes("\\") ? "\\" : "/";
    $("output").value =
      path.slice(0, path.lastIndexOf(sep)) + sep + "ChatExport_fused";
  }
  renderExports();
}

async function addExport() {
  try {
    const { paths } = await api("/api/pick-folders", { title: t("pick_export") });
    for (const p of paths || []) await addExportByPath(p);
  } catch (e) { snack(e.message); }
}

/* ---- drag & drop of export folders ----
   Browsers hide real filesystem paths, so we fingerprint the dropped
   folder's messages.html (size + sha256 of the first 4 KiB) and let the
   backend locate the matching directory on disk. */
function dirFingerprint(entry, fname = "messages.html") {
  return new Promise((resolve, reject) => {
    entry.getFile(fname, {}, fe => fe.file(f => {
      f.slice(0, 4096).arrayBuffer()
        .then(buf => crypto.subtle.digest("SHA-256", buf))
        .then(dig => {
          const hex = [...new Uint8Array(dig)]
            .map(b => b.toString(16).padStart(2, "0")).join("");
          resolve({ name: entry.name, size: f.size, sha256: hex, fname });
        }).catch(reject);
    }, reject), reject);
  });
}

function wireDrop(el, onPath) {
  ["dragover", "dragenter"].forEach(ev => el.addEventListener(ev, e => {
    e.preventDefault();
    el.classList.add("dropping");
  }));
  el.addEventListener("dragleave", e => {
    if (!el.contains(e.relatedTarget)) el.classList.remove("dropping");
  });
  el.addEventListener("drop", async e => {
    e.preventDefault();
    el.classList.remove("dropping");
    const entries = [...e.dataTransfer.items]
      .map(i => i.webkitGetAsEntry && i.webkitGetAsEntry())
      .filter(en => en && en.isDirectory);
    if (!entries.length) return snack(t("drop_only_folders"));
    for (const entry of entries) {
      try {
        snack(t("locating", { name: entry.name }));
        let sig;
        try {
          sig = await dirFingerprint(entry);
        } catch (_) {
          try {  // JSON-only exports have result.json instead
            sig = await dirFingerprint(entry, "result.json");
          } catch (_2) {
            snack(t("drop_no_messages", { name: entry.name }));
            continue;
          }
        }
        const { path } = await api("/api/locate", sig);
        if (!path) {
          snack(t("locate_fail"));
          continue;
        }
        await onPath(path);
      } catch (err) { snack(err.message); }
    }
  });
}

async function browseOutput() {
  try {
    const { path } = await api("/api/pick-folder", { title: t("pick_output") });
    if (path) $("output").value = path;
  } catch (e) { snack(e.message); }
}

function runFuse() {
  const output = $("output").value.trim();
  if (!output) return snack(t("snack_need_output"));
  startJob("/api/fuse", {
    exports: state.exports.map(e => e.path),
    output,
    page_size: chipValue("fuse-chips", "fuse-custom-n", "fuse-custom-u"),
    force: !!state.forceMix
  }, t("job_fusing"));
}

/* =============== compact =============== */
document.querySelectorAll("#compact-mode button").forEach(b => {
  b.onclick = () => {
    document.querySelectorAll("#compact-mode button").forEach(x => x.classList.remove("active"));
    b.classList.add("active");
    $("compact-files").style.display = b.dataset.mode === "files" ? "" : "none";
    $("compact-size").style.display = b.dataset.mode === "size" ? "" : "none";
    updateGoalHint();
  };
});

function renderCompactInfo() {
  const info = state.compact;
  if (!info) return;
  $("ci-name").textContent = info.name + (info.title ? " — " + info.title : "");
  $("ci-name").title = info.path;
  $("ci-sub").textContent = itemSub(info);
  const pl = $("ci-pages");
  pl.innerHTML = "";
  info.pages.slice(0, 10).forEach(p => {
    const s = document.createElement("span");
    s.textContent = p.name + " · " + p.size;
    pl.appendChild(s);
  });
  if (info.pages.length > 10) {
    const s = document.createElement("span");
    s.textContent = t("item_more", { n: info.pages.length - 10 });
    pl.appendChild(s);
  }
}

async function loadCompactPath(path) {
  state.compact = await api("/api/inspect", { path });
  renderCompactInfo();
  $("compact-sel").style.display = "none";
  $("compact-info").style.display = "";
  $("compact-btn").disabled = false;
}

async function pickCompact() {
  try {
    const { path } = await api("/api/pick-folder", { title: t("pick_compact") });
    if (path) await loadCompactPath(path);
  } catch (e) { snack(e.message); }
}

function clearCompact() {
  state.compact = null;
  $("compact-sel").style.display = "";
  $("compact-info").style.display = "none";
  $("compact-btn").disabled = true;
}

function runCompact() {
  if (!state.compact) return;
  const mode = document.querySelector("#compact-mode button.active").dataset.mode;
  const value = mode === "files"
    ? ($("files-n").value || "1")
    : chipValue("compact-chips", "compact-custom-n", "compact-custom-u");
  startJob("/api/compact",
    { export: state.compact.path, mode, value }, t("job_compacting"));
}

/* =============== enhance =============== */
function renderMeOptions() {
  const sel = $("me-select");
  const current = sel.value;
  sel.innerHTML = "";
  const none = document.createElement("option");
  none.value = "";
  none.textContent = t("me_none");
  sel.appendChild(none);
  (state.enhance ? state.enhance.senders : []).forEach(s => {
    const o = document.createElement("option");
    o.value = s.name;
    o.textContent = s.name + " (" + s.count + ")";
    sel.appendChild(o);
  });
  if ([...sel.options].some(o => o.value === current)) sel.value = current;
}

function applyEnhanceState(info) {
  state.enhance = info;
  $("ei-name").textContent = info.name + (info.title ? " — " + info.title : "");
  $("ei-name").title = info.path;
  $("ei-sub").textContent = itemSub(info);
  renderMeOptions();
  $("enhance-sel").style.display = "none";
  $("enhance-info").style.display = "";
  $("enhance-btn").disabled = false;
  $("restore-btn").style.display = info.enhanced ? "" : "none";
}

async function loadEnhancePath(path) {
  const info = await api("/api/inspect", { path });
  applyEnhanceState(info);
  if (info.enhanced) snack(t("already_enhanced"));
}

async function pickEnhance() {
  try {
    const { path } = await api("/api/pick-folder", { title: t("pick_enhance") });
    if (path) await loadEnhancePath(path);
  } catch (e) { snack(e.message); }
}

function clearEnhance() {
  state.enhance = null;
  $("enhance-sel").style.display = "";
  $("enhance-info").style.display = "none";
  $("enhance-btn").disabled = true;
  $("restore-btn").style.display = "none";
}

$("opt-bubbles").onchange = () => {
  $("bubbles-sub").classList.toggle("off", !$("opt-bubbles").checked);
};

function features() {
  return {
    bubbles: $("opt-bubbles").checked,
    quotes: $("opt-quotes").checked,
    theme: $("opt-theme").checked,
    media: $("opt-media").checked,
    note: $("opt-note").checked
  };
}

function runEnhance() {
  if (!state.enhance) return;
  const f = features();
  const layout = $("layout-chips").querySelector(".chip.sel").dataset.layout;
  const me = $("me-select").value;
  if (f.bubbles && !me && layout !== "original") return snack(t("need_me"));
  startJob("/api/enhance",
    { export: state.enhance.path, me, layout, features: f,
      fullwidth: $("opt-fullwidth").checked },
    t("job_enhancing"));
}

function runRestore() {
  if (!state.enhance) return;
  startJob("/api/restore", { export: state.enhance.path }, t("job_restoring"));
}

/* =============== convert =============== */
function setConvert(info, action) {
  state.convert = info;
  state.convertAction = action;
  $("cv-name").textContent = info.name + (info.title ? " — " + info.title : "");
  $("cv-name").title = info.path;
  $("cv-sub").textContent = t("res_convert", { msgs: info.messages });
  $("cv-detected").textContent = t(
    action === "enrich" ? "det_both"
      : action === "tojson" ? "det_html"
      : action === "eo" ? "det_enriched_only" : "det_json");
  $("convert-sel").style.display = "none";
  $("convert-info").style.display = "";
  $("convert-opts").style.display = "";
  $("cv-tojson").style.display = action === "tojson" ? "" : "none";
  $("cv-tohtml").style.display = action === "tohtml" ? "" : "none";
  $("cv-enrich").style.display = action === "enrich" ? "" : "none";
  $("cv-eo").style.display = action === "eo" ? "" : "none";
  $("convert-btn").disabled = false;
  if (action === "tojson") {
    $("convert-btn-label").textContent = t("convert_btn_tojson");
    updateConvertMode();
  } else if (action === "eo") {
    updateEoMode();
  } else {
    $("convert-btn-label").textContent = t("convert_btn_" + action);
  }
}

function clearConvert() {
  state.convert = null;
  state.convertAction = null;
  $("convert-sel").style.display = "";
  $("convert-info").style.display = "none";
  $("convert-opts").style.display = "none";
  $("convert-btn").disabled = true;
}

async function loadConvertPath(path) {
  const info = await api("/api/inspect-convert", { path });
  if (info.has_html && info.has_json) {
    // both formats at once: the only useful operation is enriching the
    // official JSON with the HTML's extra data — ask harshly first
    const ok = await confirmDialog(t("both_title"), t("both_body"),
                                   t("btn_enrich"));
    if (!ok) { clearConvert(); return; }
    setConvert(info, "enrich");
  } else if (info.has_html) {
    setConvert(info, "tojson");
  } else if (info.has_json) {
    setConvert(info, "tohtml");
  } else {
    // only a result_enriched.json: let the user pick the HTML view or
    // go back to the plain official format
    setConvert(info, "eo");
  }
}

async function pickConvert() {
  try {
    const { path } = await api("/api/pick-folder", { title: t("pick_convert") });
    if (path) await loadConvertPath(path);
  } catch (e) { snack(e.message); }
}

function updateConvertMode() {
  const mode = document.querySelector("#cv-mode button.active").dataset.mode;
  $("cv-mode-hint").textContent = t("mode_hint_" + mode);
  // destructive-mode banner: always visible while "faithful" is selected,
  // with no way to dismiss it
  $("cv-faithful-warn").style.display = mode === "faithful" ? "" : "none";
}
document.querySelectorAll("#cv-mode button").forEach(b => {
  b.onclick = () => {
    document.querySelectorAll("#cv-mode button").forEach(x => x.classList.remove("active"));
    b.classList.add("active");
    updateConvertMode();
  };
});

function updateEoMode() {
  const sub = document.querySelector("#cv-eo-mode button.active").dataset.eo;
  $("cv-eo-hint").textContent = t("eo_hint_" + sub);
  // destructive-mode banner: always visible while "downgrade" is
  // selected, with no way to dismiss it
  $("cv-eo-warn").style.display = sub === "downgrade" ? "" : "none";
  $("convert-btn-label").textContent = t("convert_btn_" + sub);
}
document.querySelectorAll("#cv-eo-mode button").forEach(b => {
  b.onclick = () => {
    document.querySelectorAll("#cv-eo-mode button").forEach(x => x.classList.remove("active"));
    b.classList.add("active");
    updateEoMode();
  };
});

function runConvert() {
  if (!state.convert || !state.convertAction) return;
  const mode = state.convertAction === "eo"
    ? document.querySelector("#cv-eo-mode button.active").dataset.eo
    : state.convertAction;
  const faithful = mode === "tojson" &&
    document.querySelector("#cv-mode button.active").dataset.mode === "faithful";
  startJob("/api/convert",
    { export: state.convert.path, mode, faithful },
    t("job_converting"));
}

/* =============== job =============== */
let pollTimer = null;
let warnCount = 0;

async function startJob(endpoint, body, title) {
  try {
    await api(endpoint, body);
  } catch (e) { return snack(e.message); }
  warnCount = 0;
  $("job").classList.add("show");
  $("job-stage").textContent = title;
  $("job-pct").textContent = "";
  $("job-spin").style.display = "";
  $("job-progress").classList.add("indet");
  $("job-bar").style.width = "0%";
  $("job-warns").innerHTML = "";
  $("job-log").textContent = "";
  $("job-result").classList.remove("show");
  document.querySelectorAll(".btn.filled").forEach(b => b.disabled = true);
  $("job").scrollIntoView({ behavior: "smooth", block: "nearest" });
  pollTimer = setInterval(poll, 300);
}

function stageLabel(stage) {
  if (!stage) return t("stage_working");
  return t("stage_" + stage.key, stage);
}

function pushWarnings(warnings) {
  for (; warnCount < warnings.length; warnCount++) {
    const div = document.createElement("div");
    div.className = "warn-item";
    div.innerHTML = '<svg viewBox="0 0 24 24"><path d="M1 21h22L12 2 1 21zm12-3h-2v-2h2v2zm0-4h-2v-4h2v4z"/></svg><span></span>';
    div.querySelector("span").textContent = warnings[warnCount];
    $("job-warns").appendChild(div);
  }
}

async function poll() {
  const s = await (await fetch("/api/status")).json();
  $("job-log").textContent = s.log || "…";
  pushWarnings(s.warnings || []);
  if (s.state === "running") {
    $("job-stage").textContent = stageLabel(s.stage);
    const frac = s.stage && s.stage.frac;
    if (typeof frac === "number") {
      $("job-progress").classList.remove("indet");
      $("job-bar").style.width = Math.round(frac * 100) + "%";
      $("job-pct").textContent = Math.round(frac * 100) + "%";
    } else {
      $("job-progress").classList.add("indet");
      $("job-pct").textContent = "";
    }
    return;
  }
  clearInterval(pollTimer);
  $("job-spin").style.display = "none";
  $("job-progress").classList.remove("indet");
  $("job-bar").style.width = "100%";
  $("job-pct").textContent = "";
  document.querySelectorAll(".btn.filled").forEach(b => b.disabled = false);
  renderExports();
  if (!state.compact) $("compact-btn").disabled = true;
  if (!state.enhance) $("enhance-btn").disabled = true;
  if (!state.convert) $("convert-btn").disabled = true;
  if (state.enhance) {
    api("/api/inspect", { path: state.enhance.path })
      .then(applyEnhanceState).catch(() => {});
  }

  const r = $("job-result"), icon = $("job-ricon"), acts = $("job-actions");
  r.classList.add("show");
  acts.innerHTML = "";
  if (s.state === "done") {
    $("job-stage").textContent = t("job_done");
    icon.className = "ricon ok";
    icon.innerHTML = '<svg viewBox="0 0 24 24"><path d="M9 16.17 4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/></svg>';
    const res = s.result;
    $("job-rtitle").textContent = res.pages
      ? t("res_summary", { msgs: res.messages, pages: res.pages.length })
        + (typeof res.own === "number" && res.own > 0
           ? t("res_own", { n: res.own }) : "")
      : (typeof res.added_fields === "number"
         ? t("res_enriched", { msgs: res.messages, n: res.added_fields })
         : t("res_convert", { msgs: res.messages }))
        + (res.removed_assets ? t("res_removed_assets") : "");
    $("job-rsub").textContent =
      (res.range ? t("res_range", { a: res.range[0], b: res.range[1] }) : "")
      + (res.out_file || res.out_dir);
    const mk = (label, path, iconSvg) => {
      const b = document.createElement("button");
      b.className = "btn text";
      b.innerHTML = iconSvg;
      const sp = document.createElement("span");
      sp.textContent = label;
      b.appendChild(sp);
      b.onclick = () => api("/api/open", { path }).catch(e => snack(e.message));
      acts.appendChild(b);
    };
    const sep = res.out_dir.includes("\\") ? "\\" : "/";
    if (res.pages)
      mk(t("open_chat"), res.out_dir + sep + "messages.html",
        '<svg viewBox="0 0 24 24"><path d="M19 19H5V5h7V3H5c-1.11 0-2 .9-2 2v14c0 1.1.89 2 2 2h14c1.1 0 2-.9 2-2v-7h-2v7zM14 3v2h3.59l-9.83 9.83 1.41 1.41L19 6.41V10h2V3h-7z"/></svg>');
    if (res.out_file)
      mk(t("open_json"), res.out_file,
        '<svg viewBox="0 0 24 24"><path d="M14 2H6c-1.1 0-2 .9-2 2v16c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V8l-6-6zm4 18H6V4h7v5h5v11z"/></svg>');
    mk(t("open_folder"), res.out_dir,
      '<svg viewBox="0 0 24 24"><path d="M10 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-8l-2-2z"/></svg>');
  } else {
    $("job-stage").textContent = t("job_error");
    icon.className = "ricon err";
    icon.innerHTML = '<svg viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/></svg>';
    $("job-rtitle").textContent = t("job_failed");
    $("job-rsub").textContent = s.error || "";
  }
}

wireDrop($("fuse-card"), addExportByPath);
wireDrop($("compact-card"), loadCompactPath);
wireDrop($("enhance-card"), loadEnhancePath);
wireDrop($("convert-card"), loadConvertPath);

applyLang();
applyVerboseBtn();
if (VERBOSE) api("/api/verbose", { on: true }).catch(() => {});
window.addEventListener("load", movePill);
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):

    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj))

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index"):
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif self.path == "/api/status":
            self._json(job_status())
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._json({"error": "JSON inválido"}, 400)
        try:
            if self.path == "/api/pick-folder":
                self._json({"path": pick_folder(
                    body.get("title") or "Selecciona una carpeta")})
            elif self.path == "/api/pick-folders":
                self._json({"paths": pick_folders(
                    body.get("title") or "Selecciona carpetas")})
            elif self.path == "/api/locate":
                self._json({"path": locate_export(
                    body.get("name", ""), int(body.get("size", 0)),
                    body.get("sha256", ""),
                    body.get("fname", "messages.html"))})
            elif self.path == "/api/inspect":
                self._json(inspect_export(body["path"]))
            elif self.path == "/api/inspect-convert":
                self._json(inspect_convert(body["path"]))
            elif self.path == "/api/convert":
                start_job(lambda: do_convert(
                    body["export"], body["mode"],
                    body.get("faithful", False)))
                self._json({"ok": True})
            elif self.path == "/api/fuse":
                start_job(lambda: do_fuse(
                    body["exports"], body["output"], body["page_size"],
                    body.get("force", False)))
                self._json({"ok": True})
            elif self.path == "/api/compact":
                start_job(lambda: do_compact(
                    body["export"], body["mode"], body["value"]))
                self._json({"ok": True})
            elif self.path == "/api/enhance":
                start_job(lambda: do_enhance(
                    body["export"], body.get("me"),
                    body.get("layout", "both"), body.get("features"),
                    body.get("fullwidth", True)))
                self._json({"ok": True})
            elif self.path == "/api/restore":
                start_job(lambda: restore(Path(body["export"]).resolve()))
                self._json({"ok": True})
            elif self.path == "/api/verbose":
                VERBOSE["on"] = bool(body.get("on"))
                self._json({"ok": True})
            elif self.path == "/api/open":
                target = Path(body["path"])
                if not target.exists():
                    raise ValueError(f"No existe: {target}")
                os.startfile(str(target))
                self._json({"ok": True})
            elif self.path == "/api/shutdown":
                self._json({"ok": True})
                # server.shutdown() bloquearía si se llamara desde este
                # mismo hilo (el que atiende la petición), así que se
                # dispara desde otro hilo tras responder al navegador.
                threading.Thread(target=self.server.shutdown,
                                 daemon=True).start()
            else:
                self._json({"error": "not found"}, 404)
        except (Exception, SystemExit) as e:
            self._json({"error": str(e) or e.__class__.__name__}, 400)


def main():
    port = 8765
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", port)) == 0:  # busy -> random
            probe2 = socket.socket()
            probe2.bind(("127.0.0.1", 0))
            port = probe2.getsockname()[1]
            probe2.close()

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://localhost:{port}"
    print(f"Telegram Export Studio -> {url}")
    print("Ctrl+C para salir")
    threading.Timer(0.4, webbrowser.open, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    print("\nServidor cerrado. Hasta luego")


if __name__ == "__main__":
    main()
