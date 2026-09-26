# For developers

*[Leer en español](DEVELOPMENT.md) · [Back to the README](../README.en.md)*

## Structure

The engine is four independent Python modules, each with a single job and usable on its own from the terminal (see the [CLI reference](CLI.en.md)). They only use the standard library.

| File | What it is |
|---|---|
| `telegram_export_fuser.py` | Merging: deduplicates by message id, repaginates Telegram-style and copies media. Also holds the parser the others use. |
| `telegram_export_compactor.py` | Repaginating an export, without touching media. |
| `telegram_export_enhancer.py` | View enhancements and their exact reversal. |
| `telegram_export_converter.py` | HTML ↔ JSON conversion, enrichment and downgrade to the official format. |
| `telegram_export_studio.py` | Desktop app: a server on `127.0.0.1` with the interface, on top of the four modules. |
| `telegram_export_version.py` | Single source of the version (see [VERSIONING.md](../VERSIONING.md), in Spanish). |
| `web/index.html`, `web/app.html` | Landing page and app of the web version. |

## Desktop app: `build_aio.py`

The files in `releases/` (`.py`, `.pyw` and `.exe`, with the version in the name) **are generated, never edited by hand**. `build_aio.py` concatenates the modules into one self-contained file and, if PyInstaller is installed, also builds the `.exe`:

```bash
python build_aio.py
```

Run it again after any change to the modules.

## Web version: `build_pages.py`

The web reuses the four engine modules (not `telegram_export_studio.py`: there's no server in the browser) and runs them with Pyodide. `build_pages.py` puts `web/` and the modules together in `_site/`, which isn't versioned.

There's no need to publish it by hand: the `.github/workflows/deploy-pages.yml` workflow rebuilds it and deploys it to GitHub Pages on every push to `main` that touches a module, `web/` or the version.

To try it locally:

```bash
python build_pages.py
python -m http.server 8000 --directory _site
```

## Tools (`tools/`)

### `pack_assets.py`

Regenerates the `ASSETS_BLOB` block in `telegram_export_converter.py`: the web structure (`css/`, `js/`, `images/`) that the JSON → HTML conversion writes next to the pages, since the JSON export lacks it. Only needed if a future Telegram Desktop version changes those files:

```bash
python tools/pack_assets.py "html_export_folder"
```

### `make_safe_sample.py`

Creates a copy of an export **with no personal content**, to attach a real case to a bug report:

```bash
python tools/make_safe_sample.py "export_folder" --scrub-filenames
```

**Removes:**

- The text of every message, which becomes `mensaje`.
- Media content: every file becomes 0 bytes, keeping its extension.
- People's names: `Persona N` for senders and `Reenviado N` for forwarded authors, also in reactions, replies and mentions.
- External URLs, which become `example.invalid`.

**Keeps**, because it's usually what needs debugging: message ids, dates, order and the structure of forwards and replies. Telegram's assets (`css/`, `js/`, `images/`) are copied as they are; they contain no personal data.

- Use `--scrub-filenames` unless you need the original names: sent files often carry text written by the user.
- `--report-only` writes nothing: it only counts senders and forwarded authors. It's a quick way to check whether a private chat is being taken for a group.
- Always review the result before sharing it.

## Debug mode

Both interfaces have a hidden diagnostics mode, off by default. It logs every operation in detail without touching the code. It never includes people's names, but it does include folder and file names: review the log before sharing it.

### Web: `?debug=1`

Add `?debug=1` to the app URL. A log panel appears (on the right on wide screens, at the bottom on narrow ones) that doesn't cover the app, can be minimized and has a *Copy* button.

It logs:

- Engine start-up: timings and Pyodide version.
- Each inspected export: messages, pages and chat type.
- The pre-scan: files and estimated size.
- Each question shown and the option picked.
- Each job: start, duration and outcome (done, failed or cancelled).
- Media copying: blocked files with the exact error, slow files and the chosen concurrency.
- The rescue ZIP: files, size and Chrome's verification time.
- Full Python and JavaScript errors (the interface only shows the last line) and uncaught ones.

It's safe to leave in the published code: nobody sees it without adding the parameter.

### Desktop

Icon button next to the power button, top right. Its state is remembered between sessions. With it on, each job's log (*Show full log*) includes:

- The operation and its parameters.
- The time of each stage, with percentage and estimated time while progress is measurable.
- The copy summary when working on a copy.
- Each warning and each cancellation: when it was requested, how long it took and whether the half-done result was removed.
- The total duration and, if it fails, the full error.
