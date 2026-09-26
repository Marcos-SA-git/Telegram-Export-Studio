# Referencia de la CLI

*[Read this in English](CLI.en.md) · [Volver al README](../README.md)*

Todo lo que hace la interfaz gráfica se puede hacer también desde la terminal, de dos formas:

- Con el archivo único de escritorio y un subcomando: `python telegram_export_studio_aio_vX.Y.Z.py <comando> …`
- Con el módulo suelto de cada función, desde el repositorio: `python telegram_export_fuser.py …` (ver [Para desarrolladores](DEVELOPMENT.md)).

| Comando | Módulo | Qué hace |
|---|---|---|
| [`fuse`](#fuse--fusionar-varios-exports) | `telegram_export_fuser.py` | Fusiona varios exports en uno |
| [`compact`](#compact--repaginar-un-export) | `telegram_export_compactor.py` | Cambia el número o el tamaño de las páginas |
| [`enhance`](#enhance--mejorar-o-desmejorar) | `telegram_export_enhancer.py` | Mejora la visualización, o la deshace |
| [`convert`](#convert--convertir-entre-html-y-json) | `telegram_export_converter.py` | Convierte entre HTML y JSON |

Sin subcomando, el archivo único abre la interfaz gráfica. Con `--version` (o `-v`) imprime la versión y termina.

Los tamaños se escriben como `500KB`, `2MB`, etc.

## `fuse` — fusionar varios exports

```bash
python telegram_export_studio_aio_vX.Y.Z.py fuse export1 export2 [export3 …] [-o CARPETA] [-s TAMAÑO] [-f]
```

| Opción | Por defecto | Descripción |
|---|---|---|
| `export1 export2 …` | *(obligatorio)* | Dos o más carpetas de export, cada una con su `messages.html`. |
| `-o`, `--output CARPETA` | `ChatExport_merged` | Carpeta donde se escribe el resultado. |
| `-s`, `--page-size TAMAÑO` | `500KB` | Tamaño aproximado de cada página. `0` genera un único archivo. |
| `-f`, `--force` | — | Fusiona aunque los exports parezcan de chats distintos. Sin esta opción, el programa se detiene y avisa. |

Los mensajes se deduplican por id, y la media repetida se copia una sola vez.

## `compact` — repaginar un export

```bash
python telegram_export_studio_aio_vX.Y.Z.py compact CARPETA [--files N | --size TAMAÑO]
```

| Opción | Por defecto | Descripción |
|---|---|---|
| `CARPETA` | *(obligatorio)* | Carpeta del export. |
| `-f`, `--files N` | `1` | Número máximo de páginas. |
| `-s`, `--size TAMAÑO` | — | Tamaño aproximado de cada página, en lugar de un número de páginas. |

`--files` y `--size` son excluyentes. Reescribe los `messages*.html` en la propia carpeta y no toca la media. Para trabajar sobre una copia, usa la interfaz gráfica.

## `enhance` — mejorar o desmejorar

```bash
python telegram_export_studio_aio_vX.Y.Z.py enhance CARPETA [--me "Tu Nombre"] [--layout both|chat|original] [--no-…]
python telegram_export_studio_aio_vX.Y.Z.py enhance CARPETA --restore
```

| Opción | Por defecto | Descripción |
|---|---|---|
| `CARPETA` | *(obligatorio)* | Carpeta del export. |
| `--me "Tu Nombre"` | — | Tu nombre tal como aparece en el chat. Obligatorio con las burbujas activas y una disposición distinta de `original`. |
| `--layout` | `both` | `chat`: tus mensajes a la derecha. `original`: todos a la izquierda, como en Telegram. `both`: un botón en el chat alterna entre las dos. |
| `--no-bubbles` | — | Sin burbujas ni fondo de chat. |
| `--no-quotes` | — | Sin citas de respuesta. |
| `--no-theme` | — | Sin botón de modo claro/oscuro. |
| `--no-media` | — | Sin vídeo y audio en línea ni visor de fotos. |
| `--no-note` | — | Sin la nota final con instrucciones. |
| `--no-fullwidth` | — | Mantiene la columna centrada en vez de ocupar toda la pantalla. |
| `--restore` | — | Quita todas las mejoras y devuelve el HTML original exacto. No se combina con las demás opciones. |

Volver a ejecutar `enhance` sobre un export ya mejorado sustituye sus mejoras por las nuevas opciones.

## `convert` — convertir entre HTML y JSON

```bash
python telegram_export_studio_aio_vX.Y.Z.py convert CARPETA                  # detecta qué hacer
python telegram_export_studio_aio_vX.Y.Z.py convert CARPETA --to-json [--faithful] [-o SALIDA.json] [--indent N | --compact]
python telegram_export_studio_aio_vX.Y.Z.py convert CARPETA --to-html [-s TAMAÑO] [-f]
python telegram_export_studio_aio_vX.Y.Z.py convert CARPETA --enrich [-o SALIDA.json]
python telegram_export_studio_aio_vX.Y.Z.py convert CARPETA --downgrade [-o SALIDA.json]
```

### Detección automática

Sin `--to-json`, `--to-html`, `--enrich` ni `--downgrade`, el programa mira qué hay en la carpeta:

| La carpeta tiene | Operación |
|---|---|
| Solo HTML | `--to-json` |
| `result.json` o `result_enriched.json` | `--to-html` |
| HTML y `result.json` | Se detiene con un aviso: lo único útil es `--enrich` |

### Modos

| Modo | Lee | Escribe | ¿Destructivo? |
|---|---|---|---|
| `--to-json` | `messages*.html` | `result.json` | No |
| `--to-json --faithful` | `messages*.html` | `result.json`, y borra `messages*.html`, `css/`, `js/` e `images/` | **Sí** |
| `--to-html` | `result.json` o, si no hay, `result_enriched.json` | `messages*.html`, `css/`, `js/` e `images/` | No |
| `--enrich` | `result.json` y `messages*.html` | `result_enriched.json` | No |
| `--downgrade` | `result_enriched.json` | `result.json` | **Sí** |

- **`--to-json`** acepta exports sin procesar, fusionados, compactados o mejorados. Por defecto genera el **JSON enriquecido**: el [esquema oficial de Telegram Desktop](https://core.telegram.org/import-export) más los campos que ese formato no contempla (texto y dirección del estado de las llamadas, nombres de archivo y la marca del generador).
- **`--faithful`** deja solo las claves oficiales y borra las páginas HTML y sus recursos, para que la carpeta quede como un export JSON real. Si usas `-o`, la carpeta no se toca: el borrado solo ocurre al escribir en el propio export.
- **`--to-html`** incluye la estructura web (`css/`, `js/`, `images/`) que el export JSON no trae; va embebida en el propio programa. Con `-f`/`--force` sobrescribe páginas existentes.
- **`--enrich`** combina los dos formatos: el `result.json` oficial tiene datos que el HTML no tiene (`from_id`, fechas de edición, tamaños…) y recibe los campos extra que solo están en el HTML. Ninguno de los originales se modifica.
- **`--downgrade`** sirve cuando solo queda un `result_enriched.json`: quita los campos extra y deja un `result.json` en formato oficial. Esos datos no se pueden recuperar sin el HTML original.

### Otras opciones

| Opción | Por defecto | Descripción |
|---|---|---|
| `-o`, `--output SALIDA.json` | `result.json` o `result_enriched.json` en la carpeta | Archivo de salida de `--to-json`, `--enrich` y `--downgrade`. |
| `--indent N` / `--compact` | `1` | Sangría del JSON, o todo en una línea. |
| `-s`, `--page-size TAMAÑO` | `500KB` | Tamaño de página para `--to-html`. `0` genera un único archivo. |
| `-f`, `--force` | — | `--to-html`: sobrescribe los `messages*.html` existentes. |

### Qué contiene el JSON

Sigue el esquema oficial: `id`, `type`, `date` (ISO 8601) y `date_unixtime`, `from`, `text` y `text_entities` (`plain`, `link`, `text_link`, `bold`, `italic`, `custom_emoji`…), `reply_to_message_id`, `forwarded_from`, `reactions`, la media (`photo`, o `file` con `media_type`, `duration_seconds` y `thumbnail`) y las llamadas como mensajes de servicio (`action: phone_call`, `actor`, `duration_seconds`, `discard_reason`).

El HTML no contiene algunos datos del JSON oficial, así que no se pueden recuperar al convertir: `from_id`, fechas de edición, y tamaños y `mime_type` de la media.

### Seguridad

- **La media nunca se copia:** los dos formatos la enlazan por la misma ruta relativa (`photos/…`, `video_files/…`). Por eso el resultado se escribe dentro de la carpeta del export; si lo guardas en otro sitio con `-o`, esos enlaces dejarán de funcionar desde ahí.
- **Sin sobrescrituras accidentales:** `--to-json`, `--enrich` y `--downgrade` se niegan a sobrescribir un `result.json` o `result_enriched.json` que no haya generado esta herramienta (lo reconocen por la marca `generated_by` / `enriched_by`). `--to-html` no sobrescribe páginas existentes sin `--force`.
