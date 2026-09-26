# Versionado

Telegram Export Studio usa [versionado semántico](https://semver.org/lang/es/): `MAYOR.MENOR.PARCHE`, por ejemplo `2.0.0`.

La versión vive en un único sitio, [`telegram_export_version.py`](telegram_export_version.py). Todo lo demás la lee de ahí: el nombre de los archivos de `releases/`, la cabecera del código, el mensaje al arrancar y el pie de la interfaz.

## Qué significa cada número

| Número | Sube cuando… | Ejemplos |
|---|---|---|
| **MAYOR** | Algo que ya funcionaba deja de funcionar igual. | Un export procesado se comporta distinto con otra versión, una opción de la CLI cambia de nombre o de efecto, el formato de salida cambia de forma incompatible. |
| **MENOR** | Se puede hacer algo nuevo sin que cambie lo anterior. | Un módulo, subcomando u opción nuevos, una mejora de rendimiento notable. |
| **PARCHE** | Se corrige algo que no funcionaba bien, sin añadir nada. | Arreglos de errores y ajustes menores. |

### Cambios mayores

- **2.0.0:** los exports mejorados guardan ahora una copia del marcado original de cada audio y vídeo (atributo `data-orig`), para poder desmejorarlos de forma exacta. Las versiones 1.x no reconocen ese formato, así que no pueden desmejorar esos elementos en un export mejorado con la 2.0.0 o posterior. Además, en la interfaz, el botón *Desmejorar* separado se sustituye por un botón que se adapta a las opciones marcadas.

## Cómo saber qué versión tienes

| Dónde | Cómo |
|---|---|
| App de escritorio | En el nombre del archivo (`…vX.Y.Z…`), en la cabecera del código, al arrancar y en el pie de la interfaz. |
| Terminal | `python telegram_export_studio_aio_vX.Y.Z.py --version` (o `-v`). |
| Repositorio | [`telegram_export_version.py`](telegram_export_version.py) en ese commit. |
| Web | En el pie de la app. Siempre es la última versión: se reconstruye en cada push a `main`, así que nunca queda desactualizada. |

## Al publicar una nueva versión

1. Cambia `VERSION` en `telegram_export_version.py`.
2. Ejecuta `python build_aio.py`. Regenera los archivos de `releases/` y borra los de la versión anterior.
3. Haz commit y push a `main`. La web se actualiza sola.
4. Crea el release en GitHub con la etiqueta `vX.Y.Z`, adjunta los tres archivos de `releases/` y describe qué cambió, usando la tabla de arriba para justificar qué número sube.
