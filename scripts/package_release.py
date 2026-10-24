"""Build a source distribution; never include user ledgers, images or credentials."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from localledger import __version__


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default=str(ROOT.parent / 'releases'))
    args = parser.parse_args()
    destination = Path(args.output).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    files = [ROOT / name for name in ['README.md', '.gitignore', 'run.py', 'desktop.py', 'requirements-packaging-windows.in', 'start.sh', '启动账本.bat',
        '启动外网试用.bat', '启动测试版.bat', '用Chrome打开账本.bat', '安装截图识别.bat', 'requirements-ocr.in', 'requirements-ocr.lock',
        'requirements-dev.in', 'requirements-dev.lock', 'ruff.toml',
        'requirements-packaging.in', 'requirements-packaging-linux.lock']]
    for folder, suffixes in [('localledger', {'.py', '.html', '.css', '.js', '.svg'}),
                             ('scripts', {'.py', '.json'}), ('tests', {'.py'}), ('docs', {'.md'}),
                             ('deploy', {'.example'}), ('devtools', {'.json'})]:
        files.extend(p for p in (ROOT / folder).rglob('*') if p.is_file() and p.suffix in suffixes and '__pycache__' not in p.parts)
    archive = destination / f'local-ledger-{__version__}.zip'
    manifest = {str(p.relative_to(ROOT)).replace('\\', '/'): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
        for file in sorted(files):
            z.write(file, str(file.relative_to(ROOT)))
        z.writestr('SOURCE-MANIFEST.json', json.dumps(manifest, ensure_ascii=False, indent=2))
    print(json.dumps({'archive': str(archive), 'files': len(files), 'bytes': archive.stat().st_size,
                      'sha256': hashlib.sha256(archive.read_bytes()).hexdigest()}))
    skill_root = ROOT / '.agents/skills'
    if (skill_root / 'sources.json').is_file():
        development_files = [p for p in skill_root.rglob('*') if p.is_file() and '__pycache__' not in p.parts]
        development_files += [ROOT / p for p in ['requirements-dev.in', 'requirements-dev.lock', 'ruff.toml',
            'scripts/verify_skills.py', 'docs/工具与优化.md', 'scripts/verify_packaging_tools.py',
            'docs/本地打包工具.md', 'requirements-packaging.in', 'requirements-packaging-linux.lock']]
        development_files += list((ROOT / 'devtools').glob('*.json'))
        kit = destination / f'ledger-development-kit-{__version__}.zip'
        with zipfile.ZipFile(kit, 'w', zipfile.ZIP_DEFLATED) as z:
            hashes = {}
            for p in sorted(development_files):
                name = str(p.relative_to(ROOT)).replace('\\', '/')
                z.write(p, name)
                hashes[name] = hashlib.sha256(p.read_bytes()).hexdigest()
            z.writestr('DEVELOPMENT-MANIFEST.json', json.dumps(hashes, ensure_ascii=False, indent=2))
        print(json.dumps({'development_kit': str(kit), 'files': len(development_files), 'bytes': kit.stat().st_size}))


if __name__ == '__main__':
    main()
