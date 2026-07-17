#!/usr/bin/env python3
"""Regenera el blob de assets embebido en telegram_export_converter.py.

Los assets estáticos (css/, js/, images/) que Telegram Desktop incluye
en todo export HTML se empaquetan como JSON {ruta: base64}, se
comprimen con zlib y se incrustan en el módulo como base64, para que
la conversión JSON→HTML pueda regenerar la estructura web completa.

Uso (con cualquier export HTML de Telegram como fuente):
    python tools/pack_assets.py "carpeta_del_export_html"
"""

import base64
import json
import re
import sys
import zlib
from pathlib import Path

MODULE = Path(__file__).parent.parent / "telegram_export_converter.py"
ASSET_DIRS = ("css", "js", "images")


def main():
    if len(sys.argv) != 2:
        sys.exit(f"uso: python {sys.argv[0]} <carpeta_export_html>")
    src = Path(sys.argv[1]).resolve()
    assets = {}
    for sub in ASSET_DIRS:
        base = src / sub
        if not base.is_dir():
            sys.exit(f"error: falta la carpeta {base}")
        for f in sorted(base.rglob("*")):
            if f.is_file():
                rel = f.relative_to(src).as_posix()
                assets[rel] = base64.b64encode(f.read_bytes()).decode()
    # el CSS/JS de un export es estático (no contiene datos del chat)
    blob = base64.b64encode(
        zlib.compress(json.dumps(assets).encode(), 9)).decode()

    text = MODULE.read_text(encoding="utf-8")
    new = re.sub(r'ASSETS_BLOB = "[^"]*"',
                 f'ASSETS_BLOB = "{blob}"', text, count=1)
    if new == text and blob not in text:
        sys.exit("error: no se encontró ASSETS_BLOB en el módulo")
    MODULE.write_text(new, encoding="utf-8")
    print(f"OK: {len(assets)} assets ({len(blob) // 1024} KB de blob) "
          f"incrustados en {MODULE.name}")


if __name__ == "__main__":
    main()
