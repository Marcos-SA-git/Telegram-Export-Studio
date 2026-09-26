# CLI reference

*[Leer en español](CLI.md) · [Back to the README](../README.en.md)*

Everything the graphical interface does can also be done from the terminal, in two ways:

- With the single desktop file and a subcommand: `python telegram_export_studio_aio_vX.Y.Z.py <command> …`
- With each function's standalone module, from the repository: `python telegram_export_fuser.py …` (see [For developers](DEVELOPMENT.en.md)).

| Command | Module | What it does |
|---|---|---|
| [`fuse`](#fuse--merge-several-exports) | `telegram_export_fuser.py` | Merges several exports into one |
| [`compact`](#compact--repaginate-an-export) | `telegram_export_compactor.py` | Changes the number or size of the pages |
| [`enhance`](#enhance--enhance-or-un-enhance) | `telegram_export_enhancer.py` | Enhances the view, or undoes it |
| [`convert`](#convert--convert-between-html-and-json) | `telegram_export_converter.py` | Converts between HTML and JSON |

Without a subcommand, the single file opens the graphical interface. With `--version` (or `-v`) it prints the version and exits.

Sizes are written as `500KB`, `2MB`, etc.

## `fuse` — merge several exports

```bash
python telegram_export_studio_aio_vX.Y.Z.py fuse export1 export2 [export3 …] [-o FOLDER] [-s SIZE] [-f]
```

| Option | Default | Description |
|---|---|---|
| `export1 export2 …` | *(required)* | Two or more export folders, each with its `messages.html`. |
| `-o`, `--output FOLDER` | `ChatExport_merged` | Folder the result is written to. |
| `-s`, `--page-size SIZE` | `500KB` | Approximate size of each page. `0` produces a single file. |
| `-f`, `--force` | — | Merges even if the exports look like different chats. Without it, the program stops and warns you. |

Messages are deduplicated by id, and repeated media is copied only once.

## `compact` — repaginate an export

```bash
python telegram_export_studio_aio_vX.Y.Z.py compact FOLDER [--files N | --size SIZE]
```

| Option | Default | Description |
|---|---|---|
| `FOLDER` | *(required)* | The export folder. |
| `-f`, `--files N` | `1` | Maximum number of pages. |
| `-s`, `--size SIZE` | — | Approximate size of each page, instead of a number of pages. |

`--files` and `--size` are mutually exclusive. It rewrites the `messages*.html` pages in the folder itself and doesn't touch media. To work on a copy, use the graphical interface.

## `enhance` — enhance or un-enhance

```bash
python telegram_export_studio_aio_vX.Y.Z.py enhance FOLDER [--me "Your Name"] [--layout both|chat|original] [--no-…]
python telegram_export_studio_aio_vX.Y.Z.py enhance FOLDER --restore
```

| Option | Default | Description |
|---|---|---|
| `FOLDER` | *(required)* | The export folder. |
| `--me "Your Name"` | — | Your name as it appears in the chat. Required with bubbles on and a layout other than `original`. |
| `--layout` | `both` | `chat`: your messages on the right. `original`: everything on the left, as in Telegram. `both`: a button in the chat switches between the two. |
| `--no-bubbles` | — | No bubbles or chat background. |
| `--no-quotes` | — | No reply quotes. |
| `--no-theme` | — | No light/dark mode button. |
| `--no-media` | — | No inline video and audio, no photo viewer. |
| `--no-note` | — | No final note with instructions. |
| `--no-fullwidth` | — | Keeps the centered column instead of filling the screen. |
| `--restore` | — | Removes every enhancement and gives back the exact original HTML. Not combined with the other options. |

Running `enhance` again on an enhanced export replaces its enhancements with the new options.

## `convert` — convert between HTML and JSON

```bash
python telegram_export_studio_aio_vX.Y.Z.py convert FOLDER                  # detects what to do
python telegram_export_studio_aio_vX.Y.Z.py convert FOLDER --to-json [--faithful] [-o OUT.json] [--indent N | --compact]
python telegram_export_studio_aio_vX.Y.Z.py convert FOLDER --to-html [-s SIZE] [-f]
python telegram_export_studio_aio_vX.Y.Z.py convert FOLDER --enrich [-o OUT.json]
python telegram_export_studio_aio_vX.Y.Z.py convert FOLDER --downgrade [-o OUT.json]
```

### Auto-detection

Without `--to-json`, `--to-html`, `--enrich` or `--downgrade`, the program looks at what the folder holds:

| The folder has | Operation |
|---|---|
| Only HTML | `--to-json` |
| `result.json` or `result_enriched.json` | `--to-html` |
| HTML and `result.json` | Stops with a warning: the only useful operation is `--enrich` |

### Modes

| Mode | Reads | Writes | Destructive? |
|---|---|---|---|
| `--to-json` | `messages*.html` | `result.json` | No |
| `--to-json --faithful` | `messages*.html` | `result.json`, and deletes `messages*.html`, `css/`, `js/` and `images/` | **Yes** |
| `--to-html` | `result.json` or, if missing, `result_enriched.json` | `messages*.html`, `css/`, `js/` and `images/` | No |
| `--enrich` | `result.json` and `messages*.html` | `result_enriched.json` | No |
| `--downgrade` | `result_enriched.json` | `result.json` | **Yes** |

- **`--to-json`** accepts raw, merged, compacted or enhanced exports. By default it produces the **enriched JSON**: [Telegram Desktop's official schema](https://core.telegram.org/import-export) plus the fields that format doesn't cover (call status text and direction, file names and the generator mark).
- **`--faithful`** keeps only the official keys and deletes the HTML pages and their assets, so the folder looks like a real JSON export. With `-o`, the folder isn't touched: the deletion only happens when writing into the export itself.
- **`--to-html`** includes the web structure (`css/`, `js/`, `images/`) the JSON export lacks; it's embedded in the program. With `-f`/`--force` it overwrites existing pages.
- **`--enrich`** combines both formats: the official `result.json` has data the HTML lacks (`from_id`, edit dates, sizes…) and receives the extra fields only found in the HTML. Neither original is modified.
- **`--downgrade`** is for when only a `result_enriched.json` is left: it removes the extra fields and leaves an official-format `result.json`. That data can't be recovered without the original HTML.

### Other options

| Option | Default | Description |
|---|---|---|
| `-o`, `--output OUT.json` | `result.json` or `result_enriched.json` in the folder | Output file for `--to-json`, `--enrich` and `--downgrade`. |
| `--indent N` / `--compact` | `1` | JSON indentation, or everything on one line. |
| `-s`, `--page-size SIZE` | `500KB` | Page size for `--to-html`. `0` produces a single file. |
| `-f`, `--force` | — | `--to-html`: overwrites existing `messages*.html` pages. |

### What the JSON contains

It follows the official schema: `id`, `type`, `date` (ISO 8601) and `date_unixtime`, `from`, `text` and `text_entities` (`plain`, `link`, `text_link`, `bold`, `italic`, `custom_emoji`…), `reply_to_message_id`, `forwarded_from`, `reactions`, media (`photo`, or `file` with `media_type`, `duration_seconds` and `thumbnail`) and calls as service messages (`action: phone_call`, `actor`, `duration_seconds`, `discard_reason`).

The HTML lacks some data of the official JSON, so it can't be recovered when converting: `from_id`, edit dates, and media sizes and `mime_type`.

### Safety

- **Media is never copied:** both formats link it by the same relative path (`photos/…`, `video_files/…`). That's why the result is written inside the export folder; if you save it elsewhere with `-o`, those links will stop working from there.
- **No accidental overwrites:** `--to-json`, `--enrich` and `--downgrade` refuse to overwrite a `result.json` or `result_enriched.json` this tool didn't generate (recognized by the `generated_by` / `enriched_by` mark). `--to-html` doesn't overwrite existing pages without `--force`.
