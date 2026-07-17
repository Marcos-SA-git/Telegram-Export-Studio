#!/usr/bin/env python3
"""Genera releases/telegram_export_studio_aio.py — versión All-In-One:
los cinco scripts (fuser, compactor, enhancer, converter y la interfaz
gráfica) en un único archivo autocontenido. Sin argumentos lanza la
interfaz; con subcomandos actúa como CLI:
    python telegram_export_studio_aio.py            -> GUI
    python telegram_export_studio_aio.py fuse ...
    python telegram_export_studio_aio.py compact ...
    python telegram_export_studio_aio.py enhance ...
    python telegram_export_studio_aio.py convert ...

También genera la copia `.pyw` (doble click sin consola) y, si
`pyinstaller` está instalado, el `.exe` autocontenido.

Pensado para que lo ejecute cualquiera que quiera su propia versión de
escritorio, incluidos usuarios finales que solo clonan el repo.

Para la versión web (GitHub Pages) usa build_pages.py en su lugar —
ese paso lo ejecuta automáticamente el workflow de GitHub Actions en
cada push, no hace falta correrlo a mano salvo para previsualizar.

Ejecuta este script tras modificar cualquier módulo:
    python build_aio.py
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).parent
MODULES = [
    ("telegram_export_fuser.py", "_fuser_main"),
    ("telegram_export_compactor.py", "_compactor_main"),
    ("telegram_export_enhancer.py", "_enhancer_main"),
    ("telegram_export_converter.py", "_converter_main"),
    ("telegram_export_studio.py", "_studio_main"),
]
RELEASES = BASE / "releases"
OUT = RELEASES / "telegram_export_studio_aio.py"
OUT_PYW = RELEASES / "Telegram Export Studio.pyw"

HEADER = '''#!/usr/bin/env python3
"""Telegram Export Studio — All-In-One.

Todas las herramientas en un único archivo autocontenido (solo
biblioteca estándar de Python 3.10+). 100% local: nada sale de tu
equipo.

    python telegram_export_studio_aio.py              -> interfaz gráfica
    python telegram_export_studio_aio.py fuse e1 e2 -o salida   [opciones]
    python telegram_export_studio_aio.py compact carpeta --files 1
    python telegram_export_studio_aio.py enhance carpeta --me "Tu Nombre"
    python telegram_export_studio_aio.py enhance carpeta --restore
    python telegram_export_studio_aio.py convert carpeta [--to-json | --to-html | --enrich | --downgrade]

GENERADO por build_aio.py — no editar a mano; edita los módulos
telegram_export_*.py y regenera.
"""

'''

FOOTER_CODE = '''

# ===========================================================================
# Punto de entrada All-In-One
# ===========================================================================

def main():
    import os
    import sys
    # Bajo pythonw.exe (doble click en el .pyw / .exe sin consola) no hay
    # stdout/stderr y cualquier print() rompería: redirigir a devnull.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    commands = {
        "fuse": _fuser_main,
        "compact": _compactor_main,
        "enhance": _enhancer_main,
        "convert": _converter_main,
        "gui": _studio_main,
    }
    if len(sys.argv) > 1 and sys.argv[1] in commands:
        cmd = sys.argv.pop(1)
        commands[cmd]()
    else:
        _studio_main()


if __name__ == "__main__":
    main()
'''

CROSS_IMPORT_RE = re.compile(
    r"^import telegram_export_fuser as tef\n"
    r"|^from telegram_export_\w+ import \([^)]*\)\n"
    r"|^from telegram_export_\w+ import [^\n(]*\n",
    re.MULTILINE)
MAIN_GUARD_RE = re.compile(
    r'\n*if __name__ == "__main__":\n    main\(\)\n*\Z')


def build_aio():
    RELEASES.mkdir(exist_ok=True)
    parts = [HEADER]
    for filename, main_name in MODULES:
        src = (BASE / filename).read_text(encoding="utf-8")
        src = src.replace("#!/usr/bin/env python3\n", "", 1)
        src = CROSS_IMPORT_RE.sub("", src)
        src = src.replace("tef.progress_hook = _progress",
                          "progress_hook = _progress")
        src = MAIN_GUARD_RE.sub("\n", src)
        assert "def main():" in src, filename
        src = src.replace("def main():", f"def {main_name}():", 1)
        assert not re.search(r"^\s*(from|import)\s+telegram_export_",
                             src, re.MULTILINE), \
            f"{filename}: queda un import cruzado sin resolver"
        section = filename.removesuffix(".py").replace("telegram_export_", "")
        parts.append(
            f"\n# {'=' * 75}\n# {section.upper()}  (de {filename})\n"
            f"# {'=' * 75}\n\n{src}")
    parts.append(FOOTER_CODE)
    OUT.write_text("".join(parts), encoding="utf-8")
    compile(OUT.read_text(encoding="utf-8"), str(OUT), "exec")
    print(f"OK {OUT.name} ({OUT.stat().st_size // 1024} KB)")
    # Versión de doble click para Windows: mismo contenido, extensión .pyw
    # (pythonw.exe la ejecuta sin abrir ninguna consola).
    shutil.copy2(OUT, OUT_PYW)
    print(f"OK {OUT_PYW.name} (doble click, sin consola)")


def build_exe():
    """Compila releases/TelegramExportStudio.exe con PyInstaller, si está
    disponible. No es un requisito: el .py y el .pyw ya funcionan sin él."""
    if shutil.which("pyinstaller") is None:
        try:
            import PyInstaller  # noqa: F401
        except ImportError:
            print("-- PyInstaller no está instalado: se omite el .exe "
                  "(pip install pyinstaller para generarlo)")
            return

    exe_path = RELEASES / "TelegramExportStudio.exe"
    if exe_path.exists():
        try:
            exe_path.unlink()
        except PermissionError:
            print(f"-- {exe_path.name} está en uso (¿abierto o en "
                  "ejecución?); ciérralo y vuelve a ejecutar build_aio.py "
                  "para actualizar el .exe")
            return

    build_dir = BASE / "build"
    spec_dir = build_dir
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile", "--noconsole", "--name", "TelegramExportStudio",
        "--distpath", str(RELEASES),
        "--workpath", str(build_dir),
        "--specpath", str(spec_dir),
        str(OUT),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    shutil.rmtree(build_dir, ignore_errors=True)
    if result.returncode != 0 or not exe_path.exists():
        print("-- Falló la compilación del .exe:")
        print(result.stdout[-1500:])
        print(result.stderr[-1500:])
        return
    print(f"OK {exe_path.name} ({exe_path.stat().st_size // 1024 // 1024} MB)")


if __name__ == "__main__":
    build_aio()
    build_exe()
