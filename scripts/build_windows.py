"""Build a Windows portable folder and ZIP, without including user data."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from localledger import __version__


def main():
    parser = argparse.ArgumentParser(description='构建 Windows 便携版')
    parser.add_argument('--output', default=str(ROOT / 'dist/windows'))
    parser.add_argument('--build-dir', default=str(ROOT / 'build/windows'))
    args = parser.parse_args()
    if sys.platform != 'win32':
        parser.error('Windows EXE 必须在 Windows 上构建')
    if sys.version_info[:2] != (3, 12):
        parser.error('请使用含 tkinter 的完整 Python 3.12 构建')
    if struct.calcsize('P') != 8 or platform.machine().lower() not in ('amd64', 'x86_64'):
        parser.error('本构建脚本生成 Windows x64 包，请使用 x64 Python')
    import tkinter
    from importlib.metadata import version
    version('pyinstaller')
    if not tkinter.TclVersion:
        parser.error('构建环境缺少 tkinter')
    output = Path(args.output).resolve()
    build = Path(args.build_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    build.mkdir(parents=True, exist_ok=True)
    name = 'XiaoGuoLedger'
    bundle = output / name
    if bundle.exists():
        parser.error(f'输出目录已存在：{bundle}。请换一个 --output，避免覆盖既有账本。')
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--onedir', '--windowed',
                    '--name', name, '--icon', str(ROOT / 'assets/xiaoguo.ico'),
                    '--distpath', str(output), '--workpath', str(build / 'work'),
                    '--specpath', str(build), '--paths', str(ROOT),
                    '--add-data', str(ROOT / 'localledger/web') + ';localledger/web',
                    '--add-data', str(ROOT / 'assets/xiaoguo.ico') + ';assets',
                    '--hidden-import', 'pystray._win32',
                    '--exclude-module', 'paddleocr', '--exclude-module', 'paddle',
                    '--exclude-module', 'PyQt6', '--exclude-module', 'webview',
                    str(ROOT / 'desktop.py')], check=True, cwd=ROOT)
    shutil.copy2(ROOT / 'docs/Windows便携版.md', bundle / '使用说明.md')
    shutil.copy2(ROOT / 'docs/小锅菜档口上手指南.md', bundle / '小锅菜档口上手指南.md')
    (bundle / '练习账本.bat').write_text('@echo off\r\nchcp 65001 >nul\r\n'
                                        f'cd /d "%~dp0"\r\nstart "" "{name}.exe" --test-mode\r\n', encoding='utf-8')
    manifest = {str(file.relative_to(bundle)).replace('\\', '/'): hashlib.sha256(file.read_bytes()).hexdigest()
                for file in sorted(bundle.rglob('*')) if file.is_file()}
    (bundle / 'MANIFEST.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    archive = output / f'xiaoguo-ledger-{__version__}-windows-x64.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as zipped:
        for file in sorted(bundle.rglob('*')):
            if file.is_file():
                zipped.write(file, name + '/' + str(file.relative_to(bundle)).replace('\\', '/'))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix('.zip.sha256').write_text(digest + '  ' + archive.name + '\n', encoding='ascii')
    print(json.dumps({'folder': str(bundle), 'zip': str(archive), 'sha256': digest,
                      'files': len(manifest), 'pyinstaller': version('pyinstaller')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
