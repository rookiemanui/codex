#!/usr/bin/env python3
"""Download the exact official models used by this release, preserving TLS checks."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tarfile
import tempfile
import urllib.request


def sha256(path):
    with path.open('rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', default=str(Path(__file__).resolve().parents[1] / 'data'))
    args = parser.parse_args()
    base = Path(args.data_dir).resolve() / 'models/official_models'
    base.mkdir(parents=True, exist_ok=True)
    models = json.loads(Path(__file__).with_name('ocr-models.json').read_text())
    for name, model in models.items():
        target = base / name
        if target.exists():
            if all((target / relative).is_file() and sha256(target / relative) == expected
                   for relative, expected in model['files'].items()):
                print(name + ': cached files verified', flush=True)
                continue
            raise RuntimeError('Model cache checksum mismatch: ' + name + '. Preserve this directory and reinstall to a new data directory.')
        with tempfile.TemporaryDirectory(dir=base) as temp:
            temp = Path(temp)
            archive = temp / 'model.tar'
            print('Downloading official model: ' + name, flush=True)
            with urllib.request.urlopen(model['url'], timeout=60) as response, archive.open('wb') as out:
                shutil.copyfileobj(response, out)
            if archive.stat().st_size != model['bytes'] or sha256(archive) != model['archive_sha256']:
                raise RuntimeError('Downloaded model checksum mismatch: ' + name)
            extracted = temp / 'files'
            extracted.mkdir()
            with tarfile.open(archive) as tar:
                tar.extractall(extracted, filter='data')
            children = list(extracted.iterdir())
            source = children[0] if len(children) == 1 and children[0].is_dir() else extracted
            if not all((source / relative).is_file() and sha256(source / relative) == expected
                       for relative, expected in model['files'].items()):
                raise RuntimeError('Extracted model checksum mismatch: ' + name)
            shutil.move(str(source), str(target))
            print(name + ': downloaded and verified', flush=True)
    print('Local screenshot recognition models are ready.', flush=True)


if __name__ == '__main__':
    main()
