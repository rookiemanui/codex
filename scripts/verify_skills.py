"""Verify installed project skill files against their recorded commit/file hashes."""
import hashlib
import json
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1] / '.agents/skills'
    if not (root / 'sources.json').is_file():
        raise SystemExit('开发技能未安装：请将配套开发工具包解压到软件目录后重试')
    sources = json.loads((root / 'sources.json').read_text(encoding='utf-8'))
    count = 0
    for name, entry in sources.items():
        directory = (root / name).resolve()
        for filename, expected in entry['files'].items():
            file = (directory / filename).resolve()
            if not file.is_relative_to(directory) or not file.is_file():
                raise RuntimeError('Missing or unsafe skill path: ' + name + '/' + filename)
            if hashlib.sha256(file.read_bytes()).hexdigest() != expected:
                raise RuntimeError('Skill file changed: ' + name + '/' + filename)
            count += 1
    print(f'PASS: {len(sources)} installed skills, {count} source files and recorded hashes verified')


if __name__ == '__main__':
    main()
