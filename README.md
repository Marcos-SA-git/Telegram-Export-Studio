# Telegram Export Studio

*[Read this in English](README.en.md)*

**Fusiona, compacta y mejora tus exportaciones de chats de Telegram — 100% en local. Sin servidores, sin cuentas, sin telemetría: tus chats nunca salen de tu equipo.**

Telegram Desktop exporta los chats en HTML, pero cada exportación es una foto fija: si exportas el mismo chat varias veces a lo largo del tiempo acabas con carpetas duplicadas y solapadas. Telegram Export Studio las **fusiona** en un único historial sin duplicados, **compacta** las decenas de archivos `messagesN.html` en las páginas que tú quieras, y **mejora** la visualización para que parezca un chat de verdad (burbujas, citas de respuesta, modo oscuro, vídeo y audio reproducibles…) de forma **reversible**, para poder deshacerlo cuando quieras.

> **Importante — qué tipo de exportación acepta:** esta herramienta está pensada para exportaciones de **un chat concreto** (dentro del chat → menú de los tres puntos → *Exportar chat*), no para la exportación completa de todos tus datos de Telegram (*Ajustes → Avanzados → Exportar datos de Telegram*), que tiene una estructura distinta y no está soportada todavía. Ver el [issue de seguimiento](https://github.com/Marcos-SA-git/Telegram-Export-Studio/issues/2) para esta funcionalidad futura.

## Privacidad, en serio

- **Nada se sube a ningún sitio.** No hay backend: la versión de escritorio es un servidor que solo escucha en tu propio ordenador (`127.0.0.1`), y la versión web ejecuta el mismo motor Python **dentro de tu navegador** (WebAssembly, vía Pyodide).
- **Código abierto y auditable.** Todo el procesado son cinco scripts de Python con la biblioteca estándar, sin dependencias externas.
- La versión web funciona incluso **sin conexión** una vez cargada.

## Cómo usarlo

### Opción 1 · Versión web — sin instalar nada

Abre la [página publicada](https://marcos-sa-git.github.io/Telegram-Export-Studio/), pulsa *Iniciar* y elige tus carpetas de exportación. El procesado corre íntegramente en tu navegador; no se envía nada a ningún servidor.

Requiere un navegador basado en Chromium (Chrome, Edge, Opera) por la File System Access API.

### Opción 2 · Aplicación de escritorio — un solo archivo, sin tocar código

La forma más sencilla de usarlo si no quieres nada relacionado con programación. Descarga uno de estos archivos desde la [página de releases](https://github.com/Marcos-SA-git/Telegram-Export-Studio/releases/latest) y ya está:

- **`TelegramExportStudio.exe`** (Windows, sin necesidad de tener Python instalado): doble click y listo. Es la opción más cómoda si no quieres instalar nada más.
  > Al ser un `.exe` sin firmar, puede que el antivirus o SmartScreen de Windows lo marquen como sospechoso la primera vez. Es un falso positivo habitual con este tipo de ejecutables; si prefieres evitarlo, usa la opción `.pyw` de abajo.
- **`Telegram Export Studio.pyw`** (Windows, requiere Python 3.10+): doble click y se abre la interfaz en tu navegador, sin ninguna ventana de terminal. Necesitas tener [Python](https://www.python.org/downloads/) instalado marcando la casilla *"Add to PATH"* durante la instalación.
- **`telegram_export_studio_aio.py`** (cualquier sistema operativo, requiere Python 3.10+): la misma app en un único archivo `.py`. Ejecútalo desde la terminal:

  ```bash
  python telegram_export_studio_aio.py
  ```

  Esto abre la interfaz gráfica en tu navegador (en `localhost`, sin salir de tu equipo). En Windows también puedes usar `py telegram_export_studio_aio.py`; en macOS/Linux, `python3 telegram_export_studio_aio.py`.

  Si prefieres la línea de comandos en vez de la interfaz gráfica, el mismo archivo admite subcomandos:

  ```bash
  python telegram_export_studio_aio.py fuse export1 export2 -o fusionado
  python telegram_export_studio_aio.py compact fusionado --files 1
  python telegram_export_studio_aio.py enhance fusionado --me "Tu Nombre"
  python telegram_export_studio_aio.py enhance fusionado --restore
  python telegram_export_studio_aio.py convert fusionado
  ```

La interfaz está disponible en español, inglés, francés, alemán, portugués, italiano, ruso, chino, japonés, hindi y árabe (con diseño derecha-a-izquierda).

## Referencia de la CLI

Si prefieres la línea de comandos a la interfaz gráfica, estos son los cuatro comandos disponibles (con `telegram_export_studio_aio.py <comando> ...` o con el módulo suelto correspondiente — ver la tabla de la sección "Para desarrolladores").

### `fuse` — fusionar varias exportaciones en una

```bash
python telegram_export_studio_aio.py fuse export1 export2 [export3 …] -o carpeta_salida [-s TAMAÑO] [-f]
```

- `export1 export2 …` (obligatorio): carpetas de exportación de Telegram a fusionar, cada una con su `messages.html`. Puedes indicar dos o más.
- `-o, --output CARPETA`: carpeta donde se escribe el resultado fusionado. Por defecto, `ChatExport_merged`.
- `-s, --page-size TAMAÑO`: tamaño aproximado de cada página `messagesN.html` de salida, por ejemplo `500KB` o `1MB`; con `0` se genera un único archivo sin paginar. Por defecto, `500KB`.
- `-f, --force`: fusiona igualmente aunque las exportaciones parezcan pertenecer a chats distintos (por ejemplo, si tienen títulos diferentes). Sin este flag, el programa se detiene y avisa para evitar mezclar chats por error.

### `compact` — reducir el número de páginas de un export ya fusionado

```bash
python telegram_export_studio_aio.py compact carpeta [--files N | --size TAMAÑO]
```

- `carpeta` (obligatorio): la carpeta del export que quieres repaginar (contiene los `messages*.html`).
- `-f, --files N`: número máximo de páginas de salida. Por defecto, `1` (todo en un único `messages.html`).
- `-s, --size TAMAÑO`: en vez de fijar un número de páginas, fija su tamaño aproximado, por ejemplo `5MB`.
- `--files` y `--size` son excluyentes entre sí: se usa uno u otro, nunca los dos a la vez.

Esta operación reescribe los `messages*.html` in situ; no toca fotos, vídeos ni audios.

### `enhance` — mejorar (o revertir) la visualización

```bash
python telegram_export_studio_aio.py enhance carpeta [--me "Tu Nombre"] [--layout both|chat|original] [--no-bubbles] [--no-quotes] [--no-theme] [--no-media] [--no-note] [--no-fullwidth] [--restore]
```

- `carpeta` (obligatorio): la carpeta del export a mejorar (contiene los `messages*.html`).
- `--me "Tu Nombre"`: tu nombre tal y como aparece en el chat exportado. Necesario si usas la disposición `chat`, para que el programa sepa qué mensajes son tuyos y colocarlos a la derecha.
- `--layout {both,chat,original}`: disposición visual. `chat` muestra tus mensajes a la derecha como en la app de Telegram; `original` conserva el ancho completo tal cual lo exporta Telegram; `both` (por defecto) añade un conmutador para alternar entre las dos sin volver a procesar nada.
- `--no-bubbles`: desactiva las burbujas y el fondo de chat.
- `--no-quotes`: desactiva las citas de respuesta (el fragmento del mensaje citado al responder).
- `--no-theme`: desactiva el conmutador de modo claro/oscuro.
- `--no-media`: desactiva la reproducción en línea de vídeo/audio y el visor de fotos.
- `--no-note`: omite la nota final con instrucciones que añade el enhancer al export.
- `--no-fullwidth`: mantiene la columna de mensajes centrada en vez de ocupar toda la pantalla.
- `--restore`: deshace todas las mejoras aplicadas y devuelve el export a su HTML original exacto, sin pérdidas. Incompatible con el resto de flags (no hace falta combinarlo con nada).

### `convert` — convertir entre HTML y JSON (el "Conversor" de la interfaz)

```bash
python telegram_export_studio_aio.py convert carpeta                 # autodetecta
python telegram_export_studio_aio.py convert carpeta --to-json [--faithful] [-o salida.json] [--indent N | --compact]
python telegram_export_studio_aio.py convert carpeta --to-html [--page-size TAMAÑO] [--force]
python telegram_export_studio_aio.py convert carpeta --enrich [-o salida.json]
python telegram_export_studio_aio.py convert carpeta --downgrade [-o salida.json]
```

- `carpeta` (obligatorio): la carpeta del export. Sin flags, se detecta automáticamente qué contiene: HTML → se convierte a JSON; JSON o solo un `result_enriched.json` → se genera la vista HTML; **HTML + JSON a la vez → se detiene con un aviso** (lo único útil en ese caso es `--enrich`).

**HTML → JSON** (`--to-json`, funciona sobre exports sin procesar, fusionados, compactados y/o mejorados):
- Por defecto genera el modo **enriquecido**: el [esquema del export JSON oficial de Telegram Desktop](https://core.telegram.org/import-export) más campos extra que el formato oficial no contempla (texto/dirección de estado de las llamadas, nombres de archivo, marca del generador) para no perder nada del HTML.
- `--faithful`: modo **formato oficial** — solo las claves oficiales. Es **destructivo**: descarta esos campos extra Y **borra** las páginas `messages*.html` y los recursos web (`css/`, `js/`, `images/`) de la carpeta, para que quede idéntica a un export JSON real de Telegram (la interfaz lo avisa con un banner permanente).
- El JSON sigue el esquema oficial: `id`, `type`, `date` ISO 8601 y `date_unixtime`, `from`, `text` y `text_entities` (`plain`/`link`/`text_link`/`bold`/`italic`/`custom_emoji`…), `reply_to_message_id`, `forwarded_from`, `reactions`, la media (`photo`, o `file` + `media_type` + `duration_seconds` + `thumbnail`) y las llamadas como mensajes de servicio con `action: phone_call`, `actor`, `duration_seconds` y `discard_reason`. Los campos que el HTML no contiene no se pueden recuperar (`from_id`, fechas de edición, tamaños y `mime_type` de la media).
- `-o` (por defecto `result.json` en la carpeta), `--indent N` / `--compact` como de costumbre. Con `-o` la carpeta original no se toca (ni siquiera en modo `--faithful`): el borrado de páginas/recursos solo ocurre cuando el resultado se escribe en el propio directorio del export.

**JSON → HTML** (`--to-html`): regenera los `messages*.html` navegables en la misma carpeta a partir del `result.json` (oficial o generado por esta herramienta) — o de `result_enriched.json` si es lo único que hay —, incluyendo la estructura web (`css/`, `js/`, `images/`) que el export JSON no trae — va embebida en el propio script. `--force` permite sobrescribir páginas existentes.

**Ambos formatos a la vez** (`--enrich`): combina lo mejor de los dos — el `result.json` oficial (que tiene datos que el HTML no: `from_id`, ediciones, tamaños…) se enriquece con los campos extra recuperables del HTML y se escribe `result_enriched.json`, **sin tocar ninguno de los originales**.

**Solo un JSON enriquecido** (`--downgrade`): cuando la carpeta ya no conserva ni el HTML ni el `result.json` oficial (por ejemplo, tras borrarlos a mano), esta opción quita los campos y la marca que añadió el enriquecido y deja un `result.json` igual al formato oficial de Telegram. Es **destructiva**: esos datos extra (dirección/estado de llamadas, nombres de archivo…) se pierden y no se pueden recuperar sin el HTML original.

**Los archivos de media nunca se copian**: se referencian por su ruta relativa (`photos/…`, `video_files/…`), que ambos formatos de export comparten. Por ese mismo motivo el resultado se escribe **dentro de la propia carpeta del export** (guardarlo en otra ubicación rompería esas rutas relativas); si quieres el JSON en otro sitio, usa `-o` sabiendo que las referencias a la media dejarán de resolverse desde ahí.

**Protección contra sobrescrituras**: `--to-json`, `--enrich` y `--downgrade` se niegan a sobrescribir un `result.json` / `result_enriched.json` que no haya generado esta herramienta (se detecta por la marca `generated_by` / `enriched_by`), y `--to-html` nunca sobrescribe páginas `messages*.html` existentes sin `--force`. Los exports originales de Telegram no se tocan jamás por accidente.

## Compatibilidad

- Los exports mejorados siguen siendo fusionables y compactables (y viceversa), en cualquier orden. "Desmejorar" (`--restore`) devuelve el HTML original exacto, sin pérdidas.
- Las notas de voz `.ogg` no se reproducen en Safari (por el códec Opus); en Chrome, Firefox y Edge sí.
- La app web necesita un navegador basado en Chromium; la de escritorio funciona con cualquier navegador moderno.

### Móviles

- **Android**: la versión web funciona en Chrome para Android (probado en Android 14) gracias a su soporte de la File System Access API. Sin embargo, **el proceso es notablemente lento — varios segundos por archivo, incluso con los pocos assets fijos del propio export (CSS, iconos), no solo con fotos o vídeos**. No es un problema de la app: el proveedor de almacenamiento de Android (Storage Access Framework) sirve las operaciones de archivo prácticamente en serie, con un coste fijo por operación que ni el navegador ni esta herramienta pueden evitar. Para exports de más de un puñado de archivos, **se recomienda usar un ordenador** incluso si el chat es pequeño.
- **iOS / iPadOS**: no probado. Según la documentación de WebKit, Safari (y por tanto cualquier navegador en iOS, todos basados en WebKit) no implementa los métodos de selección de carpetas de la File System Access API (`showDirectoryPicker`) — solo el *Origin Private File System*, que no sirve para este caso de uso. Apple no ha anunciado planes de añadir este soporte. Es previsible que la versión web **no cargue en absoluto** en iOS, no que vaya simplemente lenta.
- Está en el roadmap una futura app nativa para Android que pueda acceder al sistema de archivos de forma más directa y paralela, evitando esta limitación — ver el [issue de seguimiento](https://github.com/Marcos-SA-git/Telegram-Export-Studio/issues/4).

## Para desarrolladores

El proyecto está escrito como cinco módulos de Python independientes, cada uno con una sola responsabilidad y usable por su cuenta desde la línea de comandos:

| Script | Qué hace |
|---|---|
| `telegram_export_fuser.py` | Fusiona varias exportaciones en una sola: deduplica por id de mensaje, re-pagina al estilo Telegram y copia la media. `python telegram_export_fuser.py export1 export2 [export3 …] -o carpeta_salida [--page-size 500KB\|1MB\|0] [-f]` |
| `telegram_export_compactor.py` | Re-pagina un historial ya fusionado sin tocar la media. `python telegram_export_compactor.py carpeta [--files N \| --size 5MB]` |
| `telegram_export_enhancer.py` | Aplica (o revierte) la visualización mejorada. `python telegram_export_enhancer.py carpeta [--me "Tu Nombre"] [--layout both\|chat\|original] [--restore]` |
| `telegram_export_converter.py` | Convierte entre HTML y JSON (ambas direcciones, con autodetección), enriquece el JSON oficial con los datos del HTML y baja un JSON enriquecido al formato oficial. `python telegram_export_converter.py carpeta [--to-json [--faithful] \| --to-html \| --enrich \| --downgrade]` |
| `telegram_export_studio.py` | Interfaz gráfica local por encima de los anteriores: arranca un servidor en `127.0.0.1` y abre el navegador. |

Los archivos de `releases/` (`telegram_export_studio_aio.py`, `.pyw`, `.exe`) son **artefactos generados**, no código fuente: los produce `build_aio.py` concatenando los cinco módulos en un único archivo autocontenido. Nunca se editan a mano — el propio archivo lo indica en su cabecera. Tras cambiar algo en los módulos, vuelve a ejecutar:

```bash
python build_aio.py
```

La versión web tiene su propio generador, `build_pages.py`, que reutiliza los mismos tres módulos (sin `telegram_export_studio.py`, ya que no hay servidor en el navegador). Un workflow de GitHub Actions (`.github/workflows/deploy-pages.yml`) lo ejecuta automáticamente en cada push que toque un módulo o la carpeta `web/`, y publica el resultado en GitHub Pages — no hace falta ningún paso manual.

### `tools/` — utilidades de mantenimiento

`tools/pack_assets.py` regenera el bloque `ASSETS_BLOB` embebido en `telegram_export_converter.py`: la estructura web (`css/`, `js/`, `images/`) que `--to-html` escribe junto a los `messages*.html` que reconstruye, ya que el export JSON no la trae. Solo hace falta ejecutarlo si Telegram cambia esos assets estáticos en una versión futura de Desktop; con cualquier export HTML reciente como fuente:

```bash
python tools/pack_assets.py "carpeta_export_html"
```

### Modo debug

Ambas interfaces tienen un modo de diagnóstico oculto, pensado para investigar problemas de rendimiento (por ejemplo, en móviles) sin tener que instrumentar el código a mano:

- **Web** (`web/app.html`): añade `?debug=1` a la URL. Abre un panel de log en la parte inferior de la pantalla (útil en el móvil, donde no siempre hay DevTools remoto a mano) con el detalle de cada intento del benchmark de concurrencia de copia de media, los archivos cuya copia individual tarde más de 500ms, y el resumen final (archivos copiados, tiempo total, archivos/s). El panel solo sigue el scroll automáticamente mientras estés al final del log; si subes a leer una línea antigua, deja de arrastrarte hacia abajo. Tiene un botón "Copiar" para volcar todo el log al portapapeles. El flag está apagado por defecto y no afecta en nada a quien no lo use — es seguro que quede en el código que se publica a GitHub Pages.
- **Escritorio / AIO** (`telegram_export_studio.py`): botón de icono junto al de apagar el servidor (arriba a la derecha). Al activarlo, el trabajo en curso imprime en el log del job (el desplegable "Ver registro completo" que ya existe en la interfaz) el timing de cada etapa: cuándo empieza, cuánto tarda, y mientras haya progreso medible, un aviso periódico con % completado y ETA. El estado se recuerda entre sesiones (`localStorage`).

## Licencia

[Apache License 2.0](LICENSE) — código abierto, uso comercial permitido, con concesión explícita de patentes.
