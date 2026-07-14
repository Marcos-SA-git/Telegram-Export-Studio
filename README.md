# Telegram Export Studio

**Fusiona, compacta y mejora exportaciones de chats de Telegram — 100% en local.
Sin servidores, sin cuentas, sin telemetría: tus chats nunca salen de tu equipo.**

Telegram Desktop exporta los chats en HTML, pero cada export es una foto fija:
si exportas el mismo chat varias veces a lo largo del tiempo acabas con carpetas
duplicadas y solapadas. Telegram Export Studio las **fusiona** en un único
historial sin duplicados, **compacta** las decenas de `messagesN.html` en las
páginas que tú quieras, y **mejora** la visualización para que parezca un chat
de verdad (burbujas, citas de respuesta, modo oscuro, vídeo y audio
reproducibles…) — de forma **reversible**.

## Privacidad, en serio

- **Nada se sube a ningún sitio.** No hay backend: la versión de escritorio es
  un servidor exclusivamente en `127.0.0.1`, y la versión web ejecuta el mismo
  motor Python **dentro de tu navegador** vía WebAssembly (Pyodide).
- **Código abierto y auditable.** Todo el procesado son cuatro scripts de
  Python de biblioteca estándar, sin dependencias.
- La versión web funciona incluso **sin conexión** una vez cargada.

## Tres formas de usarlo

### 1 · Versión web (GitHub Pages) — sin instalar nada

Abre la página publicada, pulsa *Iniciar* y elige tus carpetas de export.
Requiere un navegador basado en Chromium (Chrome, Edge, Opera) por la File
System Access API. El procesado corre íntegramente en tu navegador.

### 2 · Versión de escritorio All-In-One — un solo archivo para el usuario final

Pensada para quien solo quiere *usar* la herramienta, sin tocar código:
un único archivo que llevarte, sin que importe la estructura del
repositorio ni si tienes los cuatro módulos a mano.

**Doble click (Windows):** descarga
[`releases/Telegram Export Studio.pyw`](releases/Telegram%20Export%20Studio.pyw)
y haz doble click — se abre la interfaz en tu navegador sin ninguna ventana de
terminal (requiere tener Python 3.10+ instalado, con la casilla
*"Add to PATH"* del instalador oficial).

**Ejecutable .exe (Windows, sin Python):**
`releases/TelegramExportStudio.exe` es la misma app compilada con
PyInstaller (~11 MB, autocontenida) — ni siquiera necesitas Python
instalado. Doble click y listo.

> Nota: los .exe de PyInstaller sin firmar a veces disparan falsos
> positivos en antivirus/SmartScreen. El `.pyw` o el `.py` no tienen ese
> problema y son igual de cómodos si ya hay Python instalado.

**Desde la terminal (cualquier SO):** descarga
[`releases/telegram_export_studio_aio.py`](releases/telegram_export_studio_aio.py)
y ejecútalo (Python 3.10+, sin dependencias):

```bash
# interfaz gráfica en el navegador (localhost)
python telegram_export_studio_aio.py

# o por línea de comandos: mismos subcomandos que los módulos por
# separado (ver la sección de abajo), reunidos en un solo archivo
python telegram_export_studio_aio.py fuse export1 export2 -o fusionado
python telegram_export_studio_aio.py compact fusionado --files 1
python telegram_export_studio_aio.py enhance fusionado --me "Tu Nombre"
python telegram_export_studio_aio.py enhance fusionado --restore
```

En Windows: `py telegram_export_studio_aio.py` · macOS/Linux:
`python3 telegram_export_studio_aio.py`

#### ¿Por qué existe el All-In-One, y qué hace `build_aio.py`?

El proyecto está escrito como cuatro módulos independientes (ver más
abajo) porque así es más fácil de mantener y de leer. Pero repartir
cuatro archivos que se importan entre sí es incómodo para quien solo
quiere descargar *una cosa* y que funcione. `build_aio.py` resuelve
eso: toma los cuatro módulos fuente, quita los `import` cruzados entre
ellos, renombra sus `main()` para que no choquen entre sí y los
concatena en un único archivo autocontenido con un punto de entrada
que decide, según el primer argumento, si arrancar la interfaz gráfica
o actuar como CLI de fusión/compactación/mejora. A partir de ese mismo
archivo genera también la copia `.pyw` (idéntica, pero con la
extensión que Windows asocia a `pythonw.exe`, así no aparece ninguna
consola) y, si tienes `pyinstaller` instalado (`pip install
pyinstaller`), el `.exe` autocontenido.

En resumen: **los módulos son el código fuente real** (lo que editas)
y **el All-In-One es un artefacto generado** (lo que descarga el
usuario final). Cada vez que cambies algo en los módulos, vuelve a
ejecutar `python build_aio.py` para que las tres variantes de
`releases/` se actualicen a la vez. Nunca edites
`telegram_export_studio_aio.py` a mano: el propio archivo lo avisa en
su cabecera, y el siguiente `build_aio.py` sobrescribiría el cambio.

La versión web (GitHub Pages) tiene su propio generador,
`build_pages.py`, y se actualiza sola — ver la tabla de abajo.

### 3 · Módulos separados — el código fuente

Pensada para quien quiere *entender*, *modificar* o *automatizar* con
piezas concretas, en vez de la app entera. Cada módulo hace una sola
cosa y se puede usar solo, por línea de comandos, sin ninguno de los
otros tres:

| Script | Por qué es un módulo aparte | Uso por CLI |
|---|---|---|
| `telegram_export_fuser.py` | El corazón del proyecto: dedup por id de mensaje, re-paginado estilo Telegram y copiado de media. Útil suelto cuando solo quieres fusionar, por ejemplo en un script propio o una tarea programada. | `python telegram_export_fuser.py export1 export2 [export3 …] -o carpeta_salida [--page-size 500KB\|1MB\|0] [-f]` — avisa si los exports parecen de chats distintos; `-f`/`--force` fusiona igualmente. |
| `telegram_export_compactor.py` | Repaginar es una operación distinta a fusionar (no dedup, no copia media) y a veces la quieres sin pasar por una fusión completa — por ejemplo, para reducir un historial ya fusionado antes de compartirlo. | `python telegram_export_compactor.py carpeta [--files N \| --size 5MB]` — reescribe los `messages*.html` in situ; las fotos, vídeos y audios no se tocan. |
| `telegram_export_enhancer.py` | La visualización es opcional y reversible por diseño: quien solo quiere el HTML plano de Telegram no necesita este módulo, y quien lo usa puede deshacerlo por completo en cualquier momento. | `python telegram_export_enhancer.py carpeta [--me "Tu Nombre"] [--layout both\|chat\|original] [--no-bubbles] [--no-quotes] [--no-theme] [--no-media] [--no-note] [--no-fullwidth]` · para deshacer todo: `python telegram_export_enhancer.py carpeta --restore` |
| `telegram_export_studio.py` | La interfaz gráfica es una capa por encima de los otros tres; separarla permite usarlos sin servidor ni navegador (por ejemplo en remoto por SSH) y probarlos de forma aislada. | `python telegram_export_studio.py` — arranca el servidor local y abre el navegador; toda la interacción es desde ahí (incluido cerrar el servidor cuando termines, con el botón de la interfaz). |

Cada módulo importa de los otros solo lo estrictamente necesario
(p. ej. el enhancer reutiliza los parsers del fuser) — así que si
alguna vez quieres construir tu propia herramienta con una sola pieza,
puedes importar el módulo Python directamente en vez de invocarlo por
CLI.

La versión web usa exactamente estos mismos tres módulos (sin
`telegram_export_studio.py`: no hay servidor en el navegador). Las
páginas en sí (`web/index.html`, `web/app.html`) sí se mantienen a
mano, pero las copias del motor nunca se commitean: un workflow de
GitHub Actions (`.github/workflows/deploy-pages.yml`) ejecuta
`build_pages.py` en cada push que toque un módulo o `web/`, junta todo
en `_site/` y lo publica en GitHub Pages automáticamente — no hay
ningún paso manual de "subir a Pages" que recordar. `build_pages.py`
también se puede ejecutar en local solo para previsualizar antes de
pushear.

Idiomas de la interfaz (escritorio, web y landing): español, inglés,
francés, alemán, portugués, italiano, ruso, chino, japonés, hindi y
árabe (con diseño derecha-a-izquierda).

## Compatibilidad

- Los exports mejorados siguen siendo fusionables y compactables (y viceversa),
  en cualquier orden. "Desmejorar" devuelve el HTML original exacto.
- Las notas de voz `.ogg` no se reproducen en Safari (códec Opus); en
  Chrome/Firefox/Edge sí.
- La app web necesita Chromium; la de escritorio funciona con cualquier
  navegador moderno.

## Licencia

[Apache License 2.0](LICENSE) — código abierto, uso comercial permitido,
con concesión explícita de patentes.
