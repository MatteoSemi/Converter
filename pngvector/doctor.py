"""Noninteractive runtime/version checks; no network access."""
import importlib.metadata
import inspect
import os
from pathlib import Path
import shutil
import subprocess


def main():
    failed = False
    for name in ('Pillow', 'vtracer', 'CairoSVG', 'PySide6'):
        try:
            print(f'{name}: {importlib.metadata.version(name)}')
        except importlib.metadata.PackageNotFoundError:
            print(f'MISSING: {name}; install requirements.txt')
            failed = True
    for name, candidates, flag in (
        ('Ghostscript', ['gs', 'gswin64c', 'gswin32c'], '--version'),
        ('Potrace', ['potrace', '/workspace/.local/usr/bin/potrace'], '--version'),
    ):
        executable = next((shutil.which(c) for c in candidates if shutil.which(c)), None)
        if not executable:
            print(f'MISSING: {name}; install the system package and put it on PATH')
            failed = True
            continue
        result = subprocess.run([executable, flag], capture_output=True, text=True, timeout=15)
        print(f'{name}: {result.stdout.strip()} [{executable}]')
        failed |= result.returncode != 0
    try:
        import vtracer
        import cairocffi
        print('Cairo:', cairocffi.cairo_version_string())
        print('VTracer supported options:', inspect.signature(vtracer.convert_image_to_svg_py))
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import qVersion
        print('Qt:', qVersion(), '(GUI display tested separately)')
    except Exception as exc:
        print('Runtime import failed:', exc)
        failed = True
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
