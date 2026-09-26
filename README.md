# Telegram Export Studio

*[Read this in English](README.en.md)*

Fusiona, compacta, mejora y convierte las exportaciones de chats de Telegram. **100 % en local**: sin servidores, sin cuentas y sin telemetría. Tus chats nunca salen de tu equipo.

**[Abrir la versión web](https://marcos-sa-git.github.io/Telegram-Export-Studio/)** · [Descargar la app de escritorio](https://github.com/Marcos-SA-git/Telegram-Export-Studio/releases/latest)

## Qué hace

Telegram Desktop exporta cada chat como una foto fija: si lo exportas varias veces a lo largo del tiempo, acabas con carpetas solapadas y decenas de páginas `messagesN.html`. Esta herramienta lo resuelve con cuatro funciones:

| Función | Para qué sirve |
|---|---|
| **Fusionar** | Une varios exports del mismo chat en un único historial, sin mensajes duplicados. |
| **Compactar** | Reparte el historial en las páginas que quieras, por ejemplo todo en un solo `messages.html`. |
| **Mejorar** | Le da aspecto de chat real: burbujas, citas de respuesta, modo claro/oscuro, vídeo y audio reproducibles. |
| **Convertir** | Pasa del export HTML al JSON oficial de Telegram y viceversa. |

La interfaz está en español, inglés, francés, alemán, portugués, italiano, ruso, chino, japonés, hindi y árabe.

### Prueba sin miedo

Salvo que la app lo avise en rojo, todo es reversible y se conserva toda la información posible:

- **Fusionar** escribe en una carpeta nueva: tus exports de origen no se tocan.
- **Compactar** no pierde ningún mensaje y se puede repetir con otro tamaño cuando quieras.
- **Mejorar** se deshace desmarcando las opciones: el export vuelve a su HTML original, idéntico byte a byte.
- **Convertir** solo añade archivos. Las dos únicas excepciones se avisan en rojo antes de ejecutarlas.
- **Compactar**, **Mejorar** y **Convertir** también pueden trabajar sobre una copia, dejando el original intacto.

> **Qué exportar:** un chat concreto desde Telegram Desktop. Dentro del chat, menú ⋮ → *Exportar historial del chat*, en formato HTML.
> La exportación completa de la cuenta (*Ajustes → Avanzado → Exportar datos de Telegram*) todavía no está soportada ([issue #2](https://github.com/Marcos-SA-git/Telegram-Export-Studio/issues/2)).

## Elige cómo usarlo

| Versión | Requisitos | Ideal para |
|---|---|---|
| **Web** | Chrome, Edge u Opera de escritorio | Probar sin instalar nada |
| **`TelegramExportStudio-vX.Y.Z.exe`** | Windows | Exports grandes, sin instalar Python |
| **`Telegram Export Studio vX.Y.Z.pyw`** | Windows y Python 3.10+ | Lo mismo, sin avisos del antivirus |
| **`telegram_export_studio_aio_vX.Y.Z.py`** | Cualquier sistema y Python 3.10+ | macOS o Linux, o usar la línea de comandos |

Las tres versiones de escritorio son la misma app: abren la interfaz en tu navegador, servida desde tu propio equipo (`127.0.0.1`). Se descargan desde [Releases](https://github.com/Marcos-SA-git/Telegram-Export-Studio/releases/latest).

### Web

Abre la [página publicada](https://marcos-sa-git.github.io/Telegram-Export-Studio/), espera a que cargue el motor y elige tus carpetas. La primera carga tarda algo más; después queda en caché y funciona incluso sin conexión.

El procesado ocurre en tu navegador: el mismo motor de Python compilado a WebAssembly (Pyodide). Si el resultado va a superar los 4 GB, la web te recomendará la versión de escritorio, que es más rápida; puedes continuar igualmente.

### Escritorio

- **`.exe`**: doble click. Al no estar firmado, SmartScreen o el antivirus pueden avisar la primera vez; es un falso positivo habitual. Si prefieres evitarlo, usa el `.pyw`.
- **`.pyw`**: doble click, sin ventana de terminal. Necesita [Python](https://www.python.org/downloads/) instalado con la casilla *Add to PATH* marcada.
- **`.py`**: `python telegram_export_studio_aio_vX.Y.Z.py` (`python3` en macOS y Linux). Con subcomandos también funciona como herramienta de línea de comandos: ver la [referencia de la CLI](docs/CLI.md).

Para cerrar la app usa el botón de apagar (arriba a la derecha): cerrar la pestaña no la detiene. Si no, se cierra sola tras una hora sin actividad, nunca con una operación en curso.

## Cómo funciona cada función

### Fusionar

Añade dos o más carpetas del mismo chat; la app avisa si parecen de chats distintos. El resultado se escribe en una carpeta nueva, `ChatExport_fused`, con el tamaño de página que elijas. La media repetida en varios exports se copia una sola vez.

### Compactar

Elige cuántas páginas quieres o cuánto debe ocupar cada una. Solo se reescriben los `messages*.html`; fotos, vídeos y audios no se tocan.

### Mejorar

Cada mejora se activa por separado: burbujas y fondo de chat (con quién eres tú y la disposición de los mensajes), ancho completo, citas de respuesta, modo claro/oscuro, media en línea y una nota final con instrucciones.

Si eliges un export que ya está mejorado, la app detecta qué tiene aplicado y colorea cada opción:

| Color | Significado |
|---|---|
| 🟢 Verde | Aplicada |
| 🔴 Rojo | Aplicada, pero la has desmarcado: se quitará |
| 🔵 Azul | No aplicada, pero la has marcado: se añadirá |
| ⚪ Gris | Ni aplicada ni marcada |

El botón se adapta a lo que vayas a hacer: *Mejorar export*, *Desmejorar* o *Cambiar mejoras*. Si desmarcas todo, el export vuelve a su HTML original. Si no hay nada que hacer, el botón queda deshabilitado y la app explica por qué.

### Convertir (HTML ↔ JSON)

La app detecta qué contiene la carpeta y propone la operación adecuada:

| La carpeta tiene | Qué hace | Qué escribe |
|---|---|---|
| Solo HTML | Convierte a JSON | `result.json` |
| Solo JSON | Genera la vista HTML | `messages*.html` y `css/`, `js/`, `images/` |
| HTML y JSON | Enriquece el JSON oficial con los datos extra del HTML | `result_enriched.json` |
| Solo un JSON enriquecido | Genera la vista HTML, o lo baja a formato oficial | Lo mismo que arriba, o `result.json` |

- **Enriquecido** (por defecto): el esquema oficial de Telegram más los datos que ese formato no recoge, como el estado de las llamadas o los nombres de archivo.
- ⚠️ **Formato oficial** y **bajar a formato oficial** son destructivos: descartan esos datos extra. El formato oficial borra además las páginas HTML y `css/`, `js/`, `images/`, para que la carpeta quede como un export JSON real.
- Nunca sobrescribe un `result.json` que no haya generado la propia herramienta.
- La media se enlaza por ruta relativa, así que el resultado se escribe dentro de la carpeta del export. Si eliges *Crear una copia*, se copia el export completo, media incluida, y se convierte la copia: es la forma segura de usar los modos destructivos.

Los campos exactos del JSON están en la [referencia de la CLI](docs/CLI.md#convert--convertir-entre-html-y-json).

### Operaciones largas

- La fusión y cualquier operación sobre una copia se pueden **cancelar**; el resultado a medias se borra (en escritorio, si la carpeta de destino estaba vacía al empezar).
- Lo que modifica el propio export no se puede cancelar, porque pararlo a mitad lo dejaría inconsistente.
- En la web, no cierres ni recargues la pestaña mientras haya una operación en marcha.

## Privacidad

- **No hay servidor.** La versión de escritorio solo escucha en tu propio equipo (`127.0.0.1`) y la web ejecuta Python dentro de tu navegador.
- **Código abierto y auditable.** Todo el procesado son módulos de Python que solo usan la biblioteca estándar, sin dependencias externas.

## Límites conocidos

- **Navegadores:** la web necesita Chrome, Edge u Opera de escritorio (File System Access API). En Firefox o Safari usa la versión de escritorio, que funciona con cualquier navegador moderno.
- **Archivos bloqueados por Chrome (web):** Safe Browsing puede impedir guardar algún archivo que considere sospechoso, como ejecutables o ciertos stickers `.tgs`; a menudo son falsas alarmas. La operación sigue, y al terminar puedes descargar esos archivos en un **ZIP de rescate** para copiarlos a la carpeta del resultado. La versión de escritorio no pasa por Safe Browsing.
- **Notas de voz en Safari:** los `.ogg` (códec Opus) no se reproducen en Safari; en Chrome, Firefox y Edge sí.
- **Exports mejorados antes de la 2.0.0:** esas versiones no guardaban el título de los audios enviados como archivo, así que al desmejorarlos aparece "Audio file". Los mejorados con la 2.0.0 o posterior se restauran exactos.

### Móviles

- **Android:** la web funciona en Chrome, pero es muy lenta (varios segundos por archivo) por cómo Android da acceso a los archivos. Se recomienda usar un ordenador. Hay una app nativa en el roadmap ([issue #4](https://github.com/Marcos-SA-git/Telegram-Export-Studio/issues/4)).
- **iOS y iPadOS:** no probado. Safari no permite elegir carpetas en una web, así que previsiblemente no carga.

## Más documentación

- [Referencia de la CLI](docs/CLI.md): todos los comandos y opciones.
- [Para desarrolladores](docs/DEVELOPMENT.md): módulos, compilación, versión web, herramientas y modo debug.
- [Versionado](VERSIONING.md) y [novedades de cada versión](https://github.com/Marcos-SA-git/Telegram-Export-Studio/releases).

## Licencia

[Apache License 2.0](LICENSE): código abierto, uso comercial permitido, con concesión explícita de patentes.
