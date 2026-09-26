# Para desarrolladores

*[Read this in English](DEVELOPMENT.en.md) · [Volver al README](../README.md)*

## Estructura

El motor son cuatro módulos de Python independientes, cada uno con una sola función y usable por su cuenta desde la terminal (ver la [referencia de la CLI](CLI.md)). Solo usan la biblioteca estándar.

| Archivo | Qué es |
|---|---|
| `telegram_export_fuser.py` | Fusión: deduplica por id de mensaje, repagina al estilo de Telegram y copia la media. También contiene el parser que usan los demás. |
| `telegram_export_compactor.py` | Repaginación de un export, sin tocar la media. |
| `telegram_export_enhancer.py` | Mejoras de visualización y su reversión exacta. |
| `telegram_export_converter.py` | Conversión HTML ↔ JSON, enriquecido y bajada a formato oficial. |
| `telegram_export_studio.py` | App de escritorio: un servidor en `127.0.0.1` con la interfaz, por encima de los cuatro módulos. |
| `telegram_export_version.py` | Única fuente de la versión (ver [VERSIONING.md](../VERSIONING.md)). |
| `web/index.html`, `web/app.html` | Página de inicio y app de la versión web. |

## App de escritorio: `build_aio.py`

Los archivos de `releases/` (`.py`, `.pyw` y `.exe`, con la versión en el nombre) **se generan, no se editan a mano**. `build_aio.py` concatena los módulos en un único archivo autocontenido y, si PyInstaller está instalado, crea también el `.exe`:

```bash
python build_aio.py
```

Vuelve a ejecutarlo después de cualquier cambio en los módulos.

## Versión web: `build_pages.py`

La web reutiliza los cuatro módulos del motor (no `telegram_export_studio.py`: en el navegador no hay servidor) y los ejecuta con Pyodide. `build_pages.py` junta `web/` y los módulos en `_site/`, que no se versiona.

No hace falta publicarla a mano: el workflow `.github/workflows/deploy-pages.yml` la reconstruye y la despliega en GitHub Pages en cada push a `main` que toque un módulo, `web/` o la versión.

Para probarla en local:

```bash
python build_pages.py
python -m http.server 8000 --directory _site
```

## Herramientas (`tools/`)

### `pack_assets.py`

Regenera el bloque `ASSETS_BLOB` de `telegram_export_converter.py`: la estructura web (`css/`, `js/`, `images/`) que la conversión JSON → HTML escribe junto a las páginas, porque el export JSON no la trae. Solo hace falta si una versión futura de Telegram Desktop cambia esos archivos:

```bash
python tools/pack_assets.py "carpeta_export_html"
```

### `make_safe_sample.py`

Crea una copia de un export **sin contenido personal**, para adjuntar un caso real a un informe de error:

```bash
python tools/make_safe_sample.py "carpeta_export" --scrub-filenames
```

**Borra:**

- El texto de cada mensaje, que pasa a ser `mensaje`.
- El contenido de la media: todos los archivos quedan en 0 bytes, con su extensión.
- Los nombres de personas: `Persona N` para los remitentes y `Reenviado N` para los autores de reenvíos, también en reacciones, respuestas y menciones.
- Las URL externas, que pasan a `example.invalid`.

**Conserva**, porque es lo que suele haber que depurar: los ids de mensaje, las fechas, el orden y la estructura de reenvíos y respuestas. Los recursos de Telegram (`css/`, `js/`, `images/`) se copian tal cual; no contienen datos personales.

- Usa `--scrub-filenames` salvo que necesites los nombres originales: los archivos enviados suelen llevar texto escrito por el usuario.
- `--report-only` no escribe nada: solo cuenta remitentes y autores de reenvíos. Sirve para comprobar rápido si un chat privado se está tomando por un grupo.
- Revisa siempre el resultado antes de compartirlo.

## Modo debug

Las dos interfaces tienen un modo de diagnóstico oculto, apagado por defecto. Registra el detalle de cada operación sin tener que tocar el código. Nunca incluye nombres de personas, pero sí nombres de carpetas y archivos: revisa el log antes de compartirlo.

### Web: `?debug=1`

Añade `?debug=1` a la URL de la app. Aparece un panel de log (a la derecha en pantallas anchas, abajo en las estrechas) que no tapa la app, se puede minimizar y tiene un botón *Copiar*.

Registra:

- El arranque del motor: tiempos y versión de Pyodide.
- Cada export inspeccionado: mensajes, páginas y tipo de chat.
- El análisis previo: archivos y tamaño estimado.
- Cada pregunta mostrada y la opción elegida.
- Cada trabajo: inicio, duración y resultado (terminado, fallido o cancelado).
- La copia de media: archivos bloqueados con el error exacto, archivos lentos y la concurrencia elegida.
- El ZIP de rescate: archivos, tamaño y tiempo de verificación de Chrome.
- Los errores completos de Python y JavaScript (la interfaz solo muestra la última línea) y los no capturados.

Es seguro dejarlo en el código publicado: nadie lo ve sin añadir el parámetro.

### Escritorio

Botón con icono junto al de apagar, arriba a la derecha. Su estado se recuerda entre sesiones. Con él activado, el registro de cada trabajo (*Ver registro completo*) incluye:

- La operación y sus parámetros.
- El tiempo de cada etapa, con porcentaje y tiempo estimado mientras haya progreso medible.
- El resumen de la copia al trabajar sobre una copia.
- Cada aviso y cada cancelación: cuándo se pidió, cuánto tardó y si se borró el resultado a medias.
- La duración total y, si falla, el error completo.
