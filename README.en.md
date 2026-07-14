# Telegram Export Studio

*[Leer en español](README.md)*

**Merge, compact, and enhance your Telegram chat exports — 100% local. No servers, no accounts, no telemetry: your chats never leave your computer.**

Telegram Desktop exports chats as HTML, but each export is a snapshot: if you export the same chat multiple times over time, you end up with duplicated, overlapping folders. Telegram Export Studio **merges** them into a single, duplicate-free history, **compacts** the dozens of `messagesN.html` files into as many pages as you want, and **enhances** the look so it feels like a real chat (bubbles, reply quotes, dark mode, playable video and audio…) — all **reversibly**, so you can undo it whenever you like.

> **Important — what kind of export this accepts:** this tool is designed for exports of **a single, specific chat** (inside the chat → three-dot menu → *Export chat*), not for a full export of your entire Telegram account (*Settings → Advanced → Export Telegram data*), which has a different structure and isn't supported yet. See the [tracking issue](https://github.com/Marcos-SA-git/Telegram-Export-Studio/issues/2) for this planned feature.

## Privacy, seriously

- **Nothing is ever uploaded anywhere.** There's no backend: the desktop version is a server that only listens on your own machine (`127.0.0.1`), and the web version runs the exact same Python engine **inside your browser** (WebAssembly, via Pyodide).
- **Open source and auditable.** All the processing is four Python scripts using only the standard library, with no external dependencies.
- The web version even works **offline** once it's loaded.

## How to use it

### Option 1 · Web version — nothing to install

Open the [published page](https://marcos-sa-git.github.io/Telegram-Export-Studio/), click *Start*, and pick your export folders. All processing runs entirely in your browser; nothing is sent to any server.

Requires a Chromium-based browser (Chrome, Edge, Opera) because of the File System Access API.

### Option 2 · Desktop app — a single file, no code involved

The simplest option if you don't want anything programming-related. Download one of these files from the [releases page](https://github.com/Marcos-SA-git/Telegram-Export-Studio/releases/latest) and you're done:

- **`TelegramExportStudio.exe`** (Windows, no Python required): double-click and go. The most convenient option if you don't want to install anything else.
  > Being an unsigned `.exe`, your antivirus or Windows SmartScreen may flag it the first time you run it. This is a common false positive with this kind of executable; if you'd rather avoid it, use the `.pyw` option below.
- **`Telegram Export Studio.pyw`** (Windows, requires Python 3.10+): double-click and the interface opens in your browser, with no terminal window. You need [Python](https://www.python.org/downloads/) installed, with the *"Add to PATH"* checkbox ticked during installation.
- **`telegram_export_studio_aio.py`** (any operating system, requires Python 3.10+): the same app as a single `.py` file. Run it from a terminal:

  ```bash
  python telegram_export_studio_aio.py
  ```

  This opens the graphical interface in your browser (on `localhost`, without leaving your machine). On Windows you can also use `py telegram_export_studio_aio.py`; on macOS/Linux, `python3 telegram_export_studio_aio.py`.

  If you'd rather use the command line instead of the graphical interface, the same file accepts subcommands:

  ```bash
  python telegram_export_studio_aio.py fuse export1 export2 -o merged
  python telegram_export_studio_aio.py compact merged --files 1
  python telegram_export_studio_aio.py enhance merged --me "Your Name"
  python telegram_export_studio_aio.py enhance merged --restore
  ```

The interface is available in Spanish, English, French, German, Portuguese, Italian, Russian, Chinese, Japanese, Hindi, and Arabic (with right-to-left layout).

## CLI reference

If you prefer the command line over the graphical interface, here are the three available commands (with `telegram_export_studio_aio.py <command> ...`, or the matching standalone module — see the table in the "For developers" section).

### `fuse` — merge several exports into one

```bash
python telegram_export_studio_aio.py fuse export1 export2 [export3 …] -o output_folder [-s SIZE] [-f]
```

- `export1 export2 …` (required): Telegram export folders to merge, each containing its `messages.html`. You can pass two or more.
- `-o, --output FOLDER`: folder where the merged result is written. Defaults to `ChatExport_merged`.
- `-s, --page-size SIZE`: approximate size of each output `messagesN.html` page, e.g. `500KB` or `1MB`; `0` produces a single, unpaginated file. Defaults to `500KB`.
- `-f, --force`: merges anyway even if the exports look like they belong to different chats (e.g. mismatching titles). Without this flag, the program stops and warns you, to avoid accidentally mixing up chats.

### `compact` — reduce the number of pages of an already-merged export

```bash
python telegram_export_studio_aio.py compact folder [--files N | --size SIZE]
```

- `folder` (required): the export folder you want to re-paginate (contains the `messages*.html` files).
- `-f, --files N`: maximum number of output pages. Defaults to `1` (everything in a single `messages.html`).
- `-s, --size SIZE`: instead of a page count, set an approximate page size, e.g. `5MB`.
- `--files` and `--size` are mutually exclusive: use one or the other, never both at once.

This operation rewrites the `messages*.html` files in place; it doesn't touch photos, videos, or audio.

### `enhance` — apply (or revert) the enhanced view

```bash
python telegram_export_studio_aio.py enhance folder [--me "Your Name"] [--layout both|chat|original] [--no-bubbles] [--no-quotes] [--no-theme] [--no-media] [--no-note] [--no-fullwidth] [--restore]
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

## Compatibility

- Enhanced exports can still be merged and compacted (and vice versa), in any order. "Un-enhancing" (`--restore`) returns the exact original HTML, with no data loss.
- `.ogg` voice notes don't play in Safari (due to the Opus codec); they play fine in Chrome, Firefox, and Edge.
- The web app requires a Chromium-based browser; the desktop app works with any modern browser.

## For developers

The project is written as four independent Python modules, each with a single responsibility and usable on its own from the command line:

| Script | What it does |
|---|---|
| `telegram_export_fuser.py` | Merges several exports into one: deduplicates by message id, re-paginates Telegram-style, and copies the media. `python telegram_export_fuser.py export1 export2 [export3 …] -o output_folder [--page-size 500KB\|1MB\|0] [-f]` |
| `telegram_export_compactor.py` | Re-paginates an already-merged history without touching the media. `python telegram_export_compactor.py folder [--files N \| --size 5MB]` |
| `telegram_export_enhancer.py` | Applies (or reverts) the enhanced view. `python telegram_export_enhancer.py folder [--me "Your Name"] [--layout both\|chat\|original] [--restore]` |
| `telegram_export_studio.py` | Local graphical interface on top of the three above: starts a server on `127.0.0.1` and opens the browser. |

The files in `releases/` (`telegram_export_studio_aio.py`, `.pyw`, `.exe`) are **generated artifacts**, not source code: they're produced by `build_aio.py`, which concatenates the four modules into a single self-contained file. They are never hand-edited — the file itself says so in its header. After changing anything in the modules, re-run:

```bash
python build_aio.py
```

The web version has its own generator, `build_pages.py`, which reuses the same three modules (without `telegram_export_studio.py`, since there's no server in the browser). A GitHub Actions workflow (`.github/workflows/deploy-pages.yml`) runs it automatically on every push that touches a module or the `web/` folder, and publishes the result to GitHub Pages — no manual "publish" step needed.

## License

[Apache License 2.0](LICENSE) — open source, commercial use permitted, with an explicit patent grant.
