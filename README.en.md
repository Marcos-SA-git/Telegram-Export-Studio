# Telegram Export Studio

*[Leer en español](README.md)*

Merge, compact, enhance and convert your Telegram chat exports. **100% local**: no servers, no accounts and no telemetry. Your chats never leave your device.

**[Open the web version](https://marcos-sa-git.github.io/Telegram-Export-Studio/)** · [Download the desktop app](https://github.com/Marcos-SA-git/Telegram-Export-Studio/releases/latest)

## What it does

Telegram Desktop exports each chat as a snapshot: export it several times over the years and you end up with overlapping folders and dozens of `messagesN.html` pages. This tool fixes that with four functions:

| Function | What it's for |
|---|---|
| **Merge** | Joins several exports of the same chat into a single history, with no duplicate messages. |
| **Compact** | Splits the history into as many pages as you want, for example everything in one `messages.html`. |
| **Enhance** | Makes it look like a real chat: bubbles, reply quotes, light/dark mode, playable video and audio. |
| **Convert** | Goes from the HTML export to Telegram's official JSON and back. |

The interface is available in Spanish, English, French, German, Portuguese, Italian, Russian, Chinese, Japanese, Hindi and Arabic.

### Try it without worry

Unless the app flags it in red, everything is reversible and as much information as possible is kept:

- **Merge** writes to a new folder: your source exports are never touched.
- **Compact** loses no message and can be repeated with another size whenever you like.
- **Enhance** is undone by unticking the options: the export goes back to its original HTML, identical byte for byte.
- **Convert** only adds files. The only two exceptions are flagged in red before they run.
- **Compact**, **Enhance** and **Convert** can also work on a copy, leaving the original untouched.

> **What to export:** a single chat from Telegram Desktop. Inside the chat, ⋮ menu → *Export chat history*, in HTML format.
> The full account export (*Settings → Advanced → Export Telegram data*) is not supported yet ([issue #2](https://github.com/Marcos-SA-git/Telegram-Export-Studio/issues/2)).

## Choose how to use it

| Version | Requirements | Best for |
|---|---|---|
| **Web** | Desktop Chrome, Edge or Opera | Trying it without installing anything |
| **`TelegramExportStudio-vX.Y.Z.exe`** | Windows | Large exports, without installing Python |
| **`Telegram Export Studio vX.Y.Z.pyw`** | Windows and Python 3.10+ | The same, without antivirus warnings |
| **`telegram_export_studio_aio_vX.Y.Z.py`** | Any OS and Python 3.10+ | macOS or Linux, or using the command line |

The three desktop versions are the same app: they open the interface in your browser, served from your own computer (`127.0.0.1`). Download them from [Releases](https://github.com/Marcos-SA-git/Telegram-Export-Studio/releases/latest).

### Web

Open the [published page](https://marcos-sa-git.github.io/Telegram-Export-Studio/), wait for the engine to load and pick your folders. The first load takes a bit longer; after that it is cached and even works offline.

Processing happens in your browser: the same Python engine compiled to WebAssembly (Pyodide). If the result will exceed 4 GB, the web suggests the desktop version, which is faster; you can continue anyway.

### Desktop

- **`.exe`**: double-click. Since it isn't signed, SmartScreen or your antivirus may warn the first time; it's a common false positive. To avoid it, use the `.pyw`.
- **`.pyw`**: double-click, no terminal window. Needs [Python](https://www.python.org/downloads/) installed with *Add to PATH* ticked.
- **`.py`**: `python telegram_export_studio_aio_vX.Y.Z.py` (`python3` on macOS and Linux). With subcommands it also works as a command-line tool: see the [CLI reference](docs/CLI.en.md).

To close the app, use the power button (top right): closing the tab doesn't stop it. Otherwise it closes by itself after an hour without activity, never during an operation.

## How each function works

### Merge

Add two or more folders of the same chat; the app warns you if they look like different chats. The result is written to a new folder, `ChatExport_fused`, with the page size you choose. Media repeated across exports is copied only once.

### Compact

Choose how many pages you want, or how big each one should be. Only the `messages*.html` pages are rewritten; photos, videos and audio are untouched.

### Enhance

Each enhancement is turned on separately: bubbles and chat background (with who you are and the message layout), full width, reply quotes, light/dark mode, inline media and a final note with instructions.

If you pick an export that is already enhanced, the app detects what it has applied and colors each option:

| Color | Meaning |
|---|---|
| 🟢 Green | Applied |
| 🔴 Red | Applied, but you unticked it: it will be removed |
| 🔵 Blue | Not applied, but you ticked it: it will be added |
| ⚪ Grey | Neither applied nor ticked |

The button follows what you're about to do: *Enhance export*, *Un-enhance* or *Change enhancements*. If you untick everything, the export goes back to its original HTML. If there's nothing to do, the button stays disabled and the app explains why.

### Convert (HTML ↔ JSON)

The app detects what the folder holds and offers the right operation:

| The folder has | What it does | What it writes |
|---|---|---|
| Only HTML | Converts to JSON | `result.json` |
| Only JSON | Generates the HTML view | `messages*.html` and `css/`, `js/`, `images/` |
| HTML and JSON | Enriches the official JSON with the HTML's extra data | `result_enriched.json` |
| Only an enriched JSON | Generates the HTML view, or downgrades it to the official format | The same as above, or `result.json` |

- **Enriched** (default): Telegram's official schema plus data that format doesn't cover, such as call status or file names.
- ⚠️ **Official format** and **downgrade to official format** are destructive: they drop that extra data. The official format also deletes the HTML pages and `css/`, `js/`, `images/`, so the folder looks like a real JSON export.
- It never overwrites a `result.json` the tool didn't generate itself.
- Media is linked by relative path, so the result is written inside the export folder. If you choose *Create a copy*, the whole export is copied, media included, and the copy is converted: the safe way to use the destructive modes.

The exact JSON fields are in the [CLI reference](docs/CLI.en.md#convert--convert-between-html-and-json).

### Long operations

- Merging and any operation on a copy can be **cancelled**; the half-done result is removed (on desktop, if the destination folder was empty when it started).
- Operations that modify the export itself can't be cancelled, because stopping halfway would leave it inconsistent.
- On the web, don't close or reload the tab while an operation is running.

## Privacy

- **There is no server.** The desktop version only listens on your own computer (`127.0.0.1`) and the web runs Python inside your browser.
- **Open source and auditable.** All the processing is Python modules that only use the standard library, with no external dependencies.

## Known limitations

- **Browsers:** the web needs desktop Chrome, Edge or Opera (File System Access API). On Firefox or Safari, use the desktop version, which works with any modern browser.
- **Files blocked by Chrome (web):** Safe Browsing may refuse to save a file it considers suspicious, such as executables or some `.tgs` stickers; they are often false alarms. The operation continues, and when it finishes you can download those files in a **rescue ZIP** to copy into the result folder. The desktop version doesn't go through Safe Browsing.
- **Voice notes in Safari:** `.ogg` files (Opus codec) don't play in Safari; they do in Chrome, Firefox and Edge.
- **Exports enhanced before 2.0.0:** those versions didn't keep the title of audio files sent as files, so un-enhancing them shows "Audio file". Exports enhanced with 2.0.0 or later are restored exactly.

### Mobile

- **Android:** the web works in Chrome, but it's very slow (several seconds per file) because of how Android grants access to files. Using a computer is recommended. A native app is on the roadmap ([issue #4](https://github.com/Marcos-SA-git/Telegram-Export-Studio/issues/4)).
- **iOS and iPadOS:** untested. Safari doesn't let a web page pick folders, so it most likely won't load.

## More documentation

- [CLI reference](docs/CLI.en.md): every command and option.
- [For developers](docs/DEVELOPMENT.en.md): modules, builds, the web version, tools and debug mode.
- [Versioning](VERSIONING.md) (in Spanish) and [what's new in each version](https://github.com/Marcos-SA-git/Telegram-Export-Studio/releases).

## License

[Apache License 2.0](LICENSE): open source, commercial use allowed, with an explicit patent grant.
