#!/usr/bin/env python3
"""Genera _site/ — el sitio que se publica en GitHub Pages.

_site/ es un directorio de salida efímero (en .gitignore, nunca se
commitea): junta las dos páginas mantenidas a mano en web/ (index.html
y app.html, que cargan el motor con Pyodide) con copias frescas de los
tres módulos del motor (fuser, compactor, enhancer — la GUI de
escritorio de telegram_export_studio.py no aplica aquí, no hay
servidor en el navegador).

Este script lo ejecuta automáticamente el workflow de GitHub Actions
(.github/workflows/deploy-pages.yml) en cada push a main que toque los
módulos o web/, y sube _site/ como artefacto de Pages — así el
repositorio nunca necesita tener commiteadas copias generadas.

Úsalo a mano solo para previsualizar en local antes de pushear:
    python build_pages.py
    python -m http.server 8000 --directory _site
"""

import shutil
from pathlib import Path

from telegram_export_version import VERSION

BASE = Path(__file__).parent
SITE = BASE / "_site"
ENGINE_MODULES = [
    "telegram_export_fuser.py",
    "telegram_export_compactor.py",
    "telegram_export_enhancer.py",
]


def build_pages():
    if SITE.exists():
        shutil.rmtree(SITE)
    SITE.mkdir()

    for filename in ("index.html", "app.html"):
        html = (BASE / "web" / filename).read_text(encoding="utf-8")
        html = html.replace("__APP_VERSION__", VERSION)
        (SITE / filename).write_text(html, encoding="utf-8")
        print(f"OK _site/{filename}")

    for filename in ENGINE_MODULES:
        shutil.copy2(BASE / filename, SITE / filename)
        print(f"OK _site/{filename}")


if __name__ == "__main__":
    build_pages()
