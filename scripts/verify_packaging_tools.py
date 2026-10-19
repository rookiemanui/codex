"""Check the isolated packaging environment, without touching the live ledger."""
from importlib.metadata import version
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    cache = Path(os.environ.get('NUITKA_CACHE_DIR', Path(__file__).resolve().parents[1] / '.cache/nuitka'))
    cache.mkdir(parents=True, exist_ok=True)
    child_environment = dict(os.environ, NUITKA_CACHE_DIR=str(cache))
    values = {name: version(name) for name in ['pyinstaller', 'nuitka', 'pywebview']}
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--version'], check=True, timeout=30, env=child_environment)
    subprocess.run([sys.executable, '-m', 'nuitka', '--update-check=never', '--version'], check=True, timeout=30, env=child_environment)
    import webview
    if not callable(webview.create_window):
        raise RuntimeError('pywebview import is incomplete')
    if sys.platform == 'linux':
        from qtpy import QtWebEngineWidgets, QtWidgets
        if not QtWidgets.QApplication or not QtWebEngineWidgets.QWebEngineView:
            raise RuntimeError('Qt backend is incomplete')
        values['qt_backend_import'] = 'passed'
    values['platform'] = sys.platform
    print(json.dumps(values, ensure_ascii=False))


if __name__ == '__main__':
    main()
