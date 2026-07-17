# Telegram Export Studio

*[Leer en español](README.md)*

**Merge, compact, and enhance your Telegram chat exports — 100% local. No servers, no accounts, no telemetry: your chats never leave your computer.**

Telegram Desktop exports chats as HTML, but each export is a snapshot: if you export the same chat multiple times over time, you end up with duplicated, overlapping folders. Telegram Export Studio **merges** them into a single, duplicate-free history, **compacts** the dozens of `messagesN.html` files into as many pages as you want, and **enhances** the look so it feels like a real chat (bubbles, reply quotes, dark mode, playable video and audio…) — all **reversibly**, so you can undo it whenever you like.

> **Important — what kind of export this accepts:** this tool is designed for exports of **a single, specific chat** (inside the chat → three-dot menu → *Export chat*), not for a full export of your entire Telegram account (*Settings → Advanced → Export Telegram data*), which has a different structure and isn't supported yet. See the [tracking issue](https://github.com/Marcos-SA-git/Telegram-Export-Studio/issues/2) for this planned feature.

## Privacy, seriously

- **Nothing is ever uploaded anywhere.** There's no backend: the desktop version is a server that only listens on your own machine (`127.0.0.1`), and the web version runs the exact same Python engine **inside your browser** (WebAssembly, via Pyodide).
- **Open source and auditable.** All the processing is five Python scripts using only the standard library, with no external dependencies.
- The web version even works **offline** once it's loaded.

## How to use it

### Option 1 · Web version — nothing to install

Open the [published page](https://marcos-sa-git.github.io/Telegram-Export-Studio/), click *Start*, and pick your export folders. All processing runs entirely in your browser; nothing is sent to any server.

Requires a Chromium-based browser (Chrome, Edge, Opera) because of the File System Access API.

### Option 2 · Desktop app — a single file, no code involved

The simplest option if you don't want anything programming-related. Download one of these files from the [releases page](https://github.com/Marcos-SA-git/Telegram-Export-Studio/releases/latest) and you're done:

- **`TelegramExportStudio-vX.Y.Z.exe`** (Windows, no Python required): double-click and go. The most convenient option if you don't want to install anything else.
  > Being an unsigned `.exe`, your antivirus or Windows SmartScreen may flag it the first time you run it. This is a common false positive with this kind of executable; if you'd rather avoid it, use the `.pyw` option below.
- **`Telegram Export Studio vX.Y.Z.pyw`** (Windows, requires Python 3.10+): double-click and the interface opens in your browser, with no terminal window. You need [Python](https://www.python.org/downloads/) installed, with the *"Add to PATH"* checkbox ticked during installation.
- **`telegram_export_studio_aio_vX.Y.Z.py`** (any operating system, requires Python 3.10+): the same app as a single `.py` file. Run it from a terminal:

  ```bash
  python telegram_export_studio_aio_vX.Y.Z.py
  ```

  This opens the graphical interface in your browser (on `localhost`, without leaving your machine). On Windows you can also use `py telegram_export_studio_aio_vX.Y.Z.py`; on macOS/Linux, `python3 telegram_export_studio_aio_vX.Y.Z.py`.

  > `X.Y.Z` is whatever version you download (it's in the filename itself, in the code's header, in the startup message, and in the interface's footer — see [VERSIONING.md](VERSIONING.md)). `--version` prints it on its own and exits.

  If you'd rather use the command line instead of the graphical interface, the same file accepts subcommands:

  ```bash
  python telegram_export_studio_aio_vX.Y.Z.py fuse export1 export2 -o merged
  python telegram_export_studio_aio_vX.Y.Z.py compact merged --files 1
  python telegram_export_studio_aio_vX.Y.Z.py enhance merged --me "Your Name"
  python telegram_export_studio_aio_vX.Y.Z.py enhance merged --restore
  python telegram_export_studio_aio_vX.Y.Z.py convert merged
  ```

The interface is available in Spanish, English, French, German, Portuguese, Italian, Russian, Chinese, Japanese, Hindi, and Arabic (with right-to-left layout).

## CLI reference

If you prefer the command line over the graphical interface, here are the four available commands (with `telegram_export_studio_aio_vX.Y.Z.py <command> ...`, or the matching standalone module — see the table in the "For developers" section).

### `fuse` — merge several exports into one

```bash
python telegram_export_studio_aio_vX.Y.Z.py fuse export1 export2 [export3 …] -o output_folder [-s SIZE] [-f]
```

- `export1 export2 …` (required): Telegram export folders to merge, each containing its `messages.html`. You can pass two or more.
- `-o, --output FOLDER`: folder where the merged result is written. Defaults to `ChatExport_merged`.
- `-s, --page-size SIZE`: approximate size of each output `messagesN.html` page, e.g. `500KB` or `1MB`; `0` produces a single, unpaginated file. Defaults to `500KB`.
- `-f, --force`: merges anyway even if the exports look like they belong to different chats (e.g. mismatching titles). Without this flag, the program stops and warns you, to avoid accidentally mixing up chats.

### `compact` — reduce the number of pages of an already-merged export

```bash
python telegram_export_studio_aio_vX.Y.Z.py compact folder [--files N | --size SIZE]
```

- `folder` (required): the export folder you want to re-paginate (contains the `messages*.html` files).
- `-f, --files N`: maximum number of output pages. Defaults to `1` (everything in a single `messages.html`).
- `-s, --size SIZE`: instead of a page count, set an approximate page size, e.g. `5MB`.
- `--files` and `--size` are mutually exclusive: use one or the other, never both at once.

This operation rewrites the `messages*.html` files in place; it doesn't touch photos, videos, or audio.

### `enhance` — apply (or revert) the enhanced view

```bash
python telegram_export_studio_aio_vX.Y.Z.py enhance folder [--me "Your Name"] [--layout both|chat|original] [--no-bubbles] [--no-quotes] [--no-theme] [--no-media] [--no-note] [--no-fullwidth] [--restore]
```

- `folder` (required): the export folder to enhance (contains the `messages*.html` files).
- `--me "Your Name"`: your name exactly as it appears in the exported chat. Required if you use the `chat` layout, so the program knows which messages are yours and places them on the right.
- `--layout {both,chat,original}`: visual layout. `chat` shows your messages on the right like the Telegram app; `original` keeps the full-width layout as exported by Telegram; `both` (default) adds a toggle to switch between the two without reprocessing anything.
- `--no-bubbles`: disables bubbles and the chat background.
- `--no-quotes`: disables reply quotes (the quoted snippet shown when replying to a message).
- `--no-theme`: disables the light/dark mode toggle.
- `--no-media`: disables inline video/audio playback and the photo viewer.
- `--no-note`: skips the final note with instructions that the enhancer adds to the export.
- `--no-fullwidth`: keeps the message column centered instead of filling the whole screen.
- `--restore`: undoes all applied enhancements and returns the export to its exact original HTML, with no data loss. Not meant to be combined with the other flags.

### `convert` — convert between HTML and JSON (the UI's "Converter")

```bash
python telegram_export_studio_aio_vX.Y.Z.py convert folder                 # auto-detects
python telegram_export_studio_aio_vX.Y.Z.py convert folder --to-json [--faithful] [-o output.json] [--indent N | --compact]
python telegram_export_studio_aio_vX.Y.Z.py convert folder --to-html [--page-size SIZE] [--force]
python telegram_export_studio_aio_vX.Y.Z.py convert folder --enrich [-o output.json]
python telegram_export_studio_aio_vX.Y.Z.py convert folder --downgrade [-o output.json]
```

- `folder` (required): the export folder. Without flags, what it contains is detected automatically: HTML → converted to JSON; JSON, or just a `result_enriched.json` → the HTML view is generated; **HTML + JSON at once → it stops with a warning** (the only useful operation in that case is `--enrich`).

**HTML → JSON** (`--to-json`, works on raw, merged, compacted and/or enhanced exports):
- By default it produces the **enriched** mode: [Telegram Desktop's official JSON export schema](https://core.telegram.org/import-export) plus extra fields the official format doesn't cover (call status/direction texts, file names, generator mark) so nothing from the HTML is lost.
- `--faithful`: **official format** mode — official keys only. It is **destructive**: those extra fields are dropped AND the `messages*.html` pages and web assets (`css/`, `js/`, `images/`) are **deleted** from the folder, so it ends up matching a real Telegram JSON export exactly (the UI shows a permanent warning banner).
- The JSON follows the official schema: `id`, `type`, ISO 8601 `date` and `date_unixtime`, `from`, `text` and `text_entities` (`plain`/`link`/`text_link`/`bold`/`italic`/`custom_emoji`…), `reply_to_message_id`, `forwarded_from`, `reactions`, the media (`photo`, or `file` + `media_type` + `duration_seconds` + `thumbnail`), and phone calls as service messages with `action: phone_call`, `actor`, `duration_seconds` and `discard_reason`. Fields the HTML doesn't contain cannot be recovered (`from_id`, edit dates, media sizes and `mime_type`).
- `-o` (defaults to `result.json` in the folder), `--indent N` / `--compact` as usual. With `-o` the original folder is left untouched (even in `--faithful` mode): the page/asset deletion only happens when the result is written into the export folder itself.

**JSON → HTML** (`--to-html`): regenerates the browsable `messages*.html` pages in the same folder from the `result.json` (official or generated by this tool) — or from `result_enriched.json` if that's all there is —, including the web structure (`css/`, `js/`, `images/`) the JSON export doesn't ship — it is embedded in the script itself. `--force` allows overwriting existing pages.

**Both formats at once** (`--enrich`): combines the best of both — the official `result.json` (which holds data the HTML lacks: `from_id`, edits, sizes…) gets enriched with the extra fields recoverable from the HTML, written to `result_enriched.json` **without touching either original**.

**Enriched JSON only** (`--downgrade`): when the folder no longer has the HTML or the official `result.json` (e.g. they were deleted by hand), this strips the fields and mark enrichment added, leaving a `result.json` matching Telegram's official format. It is **destructive**: that extra data (call direction/status, file names…) is lost and can't be recovered without the original HTML.

**Media files are never copied**: they are referenced by relative path (`photos/…`, `video_files/…`), which both export layouts share. For that same reason the result is written **inside the export folder itself** (saving it elsewhere would break those relative paths); if you want the JSON somewhere else, use `-o` knowing the media references won't resolve from there.

**Overwrite protection**: `--to-json`, `--enrich` and `--downgrade` refuse to overwrite a `result.json` / `result_enriched.json` this tool did not generate (detected via the `generated_by` / `enriched_by` mark), and `--to-html` never overwrites existing `messages*.html` pages without `--force`. Telegram's original exports are never touched by accident.

## Compatibility

- Enhanced exports can still be merged and compacted (and vice versa), in any order. "Un-enhancing" (`--restore`) returns the exact original HTML, with no data loss.
- `.ogg` voice notes don't play in Safari (due to the Opus codec); they play fine in Chrome, Firefox, and Edge.
- The web app requires a Chromium-based browser; the desktop app works with any modern browser.

### Mobile

- **Android**: the web version works in Chrome for Android (tested on Android 14) thanks to its File System Access API support. However, **the process is noticeably slow — several seconds per file, even for the handful of fixed assets bundled with every export (CSS, icons), not just photos or videos**. This isn't a bug in the app: Android's storage provider (the Storage Access Framework) serves file operations essentially serially, with a fixed per-operation cost that neither the browser nor this tool can work around. For exports with more than a handful of files, **using a computer is recommended** even for small chats.
- **iOS / iPadOS**: not tested. Per WebKit's own documentation, Safari (and therefore every browser on iOS, all of which are WebKit-based) doesn't implement the File System Access API's directory-picker methods (`showDirectoryPicker`) — only the *Origin Private File System*, which doesn't fit this use case. Apple hasn't announced plans to add this support. The web version will most likely **not load at all** on iOS, rather than simply being slow.
- A native Android app that can access the filesystem more directly and in parallel, sidestepping this limitation, is on the roadmap — see the [tracking issue](https://github.com/Marcos-SA-git/Telegram-Export-Studio/issues/4).

## For developers

The project is written as five independent Python modules, each with a single responsibility and usable on its own from the command line:

| Script | What it does |
|---|---|
| `telegram_export_fuser.py` | Merges several exports into one: deduplicates by message id, re-paginates Telegram-style, and copies the media. `python telegram_export_fuser.py export1 export2 [export3 …] -o output_folder [--page-size 500KB\|1MB\|0] [-f]` |
| `telegram_export_compactor.py` | Re-paginates an already-merged history without touching the media. `python telegram_export_compactor.py folder [--files N \| --size 5MB]` |
| `telegram_export_enhancer.py` | Applies (or reverts) the enhanced view. `python telegram_export_enhancer.py folder [--me "Your Name"] [--layout both\|chat\|original] [--restore]` |
| `telegram_export_converter.py` | Converts between HTML and JSON (both directions, auto-detected), enriches the official JSON with the HTML's data, and downgrades an enriched JSON back to the official format. `python telegram_export_converter.py folder [--to-json [--faithful] \| --to-html \| --enrich \| --downgrade]` |
| `telegram_export_studio.py` | Local graphical interface on top of the modules above: starts a server on `127.0.0.1` and opens the browser. |

The files in `releases/` (`telegram_export_studio_aio_vX.Y.Z.py`, `.pyw`, `.exe`, versioned in the filename — see [VERSIONING.md](VERSIONING.md)) are **generated artifacts**, not source code: they're produced by `build_aio.py`, which concatenates the five modules into a single self-contained file. They are never hand-edited — the file itself says so in its header. After changing anything in the modules, re-run:

```bash
python build_aio.py
```

The web version has its own generator, `build_pages.py`, which reuses the same three modules (without `telegram_export_studio.py`, since there's no server in the browser). A GitHub Actions workflow (`.github/workflows/deploy-pages.yml`) runs it automatically on every push that touches a module or the `web/` folder, and publishes the result to GitHub Pages — no manual "publish" step needed.

### `tools/` — maintenance utilities

`tools/pack_assets.py` regenerates the `ASSETS_BLOB` embedded in `telegram_export_converter.py`: the web structure (`css/`, `js/`, `images/`) that `--to-html` writes alongside the `messages*.html` it rebuilds, since the JSON export doesn't ship it. You only need to run it if Telegram changes those static assets in a future Desktop version, using any recent HTML export as the source:

```bash
python tools/pack_assets.py "html_export_folder"
```

### Debug mode

Both interfaces have a hidden diagnostic mode, meant for investigating performance issues (e.g. on mobile) without having to instrument the code by hand:

- **Web** (`web/app.html`): add `?debug=1` to the URL. Opens a log panel at the bottom of the screen (useful on a phone, where remote DevTools isn't always handy) showing the detail of every media-copy concurrency benchmark attempt, any individual file copy that takes longer than 500ms, and a final summary (files copied, total time, files/s). The panel only auto-scrolls while you're at the bottom of the log; scroll up to read an older line and it stops following. It has a "Copy" button to dump the whole log to the clipboard. The flag is off by default and has zero effect on anyone not using it — it's safe to leave in the code published to GitHub Pages.
- **Desktop / AIO** (`telegram_export_studio.py`): an icon button next to the shutdown button (top right). Turning it on makes the running job print stage timing to the job log (the existing "Show full log" disclosure in the interface) — when each stage starts, how long it takes, and, while there's measurable progress, a periodic update with % complete and ETA. The setting is remembered across sessions (`localStorage`).

## License

[Apache License 2.0](LICENSE) — open source, commercial use permitted, with an explicit patent grant.
