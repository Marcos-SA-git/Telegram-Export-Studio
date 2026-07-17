# Versionado

Telegram Export Studio usa [Versionado Semántico](https://semver.org/lang/es/):
tres números separados por puntos, `MAYOR.MENOR.PARCHE` (p. ej. `1.1.0`).

La versión vive en un único sitio, [`telegram_export_version.py`](telegram_export_version.py)
— todo lo demás (el nombre de los archivos en `releases/`, la cabecera del
código, el mensaje de bienvenida de la CLI, el footer de la interfaz) la lee
de ahí.

## Qué significa cada número

Leyendo de izquierda a derecha, `MAYOR.MENOR.PARCHE`:

- **MAYOR** — cambios que rompen compatibilidad: un export procesado con una
  versión antigua deja de abrirse igual, una opción de la CLI cambia de
  nombre o de comportamiento, o el formato de salida cambia de forma
  incompatible. Sube cuando algo que ya funcionaba deja de funcionar como
  antes.
- **MENOR** — funcionalidad nueva que no rompe nada existente: un módulo
  nuevo, un subcomando nuevo, una opción nueva, una mejora de rendimiento
  notable. Sube cuando se puede hacer algo que antes no se podía, sin que
  el uso anterior cambie.
- **PARCHE** — arreglos de errores y ajustes menores que no añaden
  funcionalidad ni cambian comportamiento observable, más allá de corregir
  el bug en cuestión. Sube cuando se soluciona algo que no funcionaba bien.

## Cómo consultar la versión actual

- **Apps de escritorio (AIO):** el nombre del propio archivo la incluye
  (`telegram_export_studio_aio_vX.Y.Z.py`, `Telegram Export Studio vX.Y.Z.pyw`,
  `TelegramExportStudio-vX.Y.Z.exe`), y aparece también al principio del
  código, en el mensaje de bienvenida al arrancar la interfaz, y en el
  footer de la propia interfaz.
- **Por CLI:** `python telegram_export_studio_aio_vX.Y.Z.py --version` (o
  `-v`) la imprime y termina sin hacer nada más.
- **Módulos sueltos / repositorio:** [`telegram_export_version.py`](telegram_export_version.py)
  siempre tiene la versión vigente en el momento de ese commit.

## Al publicar una nueva versión

1. Edita `VERSION` en `telegram_export_version.py`.
2. Ejecuta `python build_aio.py` para regenerar los artefactos de
   `releases/` con el nombre y la cabecera actualizados (borra los de la
   versión anterior).
3. Escribe el changelog del release describiendo qué cambió desde la
   versión anterior, usando las categorías de arriba (MAYOR/MENOR/PARCHE)
   como guía de qué número mover.
