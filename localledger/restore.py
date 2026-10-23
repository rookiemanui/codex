"""Validate the application's existing ZIP backups before restoring a new ledger."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import tempfile
import zipfile

from .store import Problem
from .trial import MARKER, SIGNATURE


MAX_MEMBERS = 50000
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_COMPRESSION_RATIO = 1000
SUPPORTED_SCHEMA_VERSIONS = {'4'}

# Required columns of the current export format; additional columns are harmless.
REQUIRED_COLUMNS = {
    'metadata': 'key value',
    'accounts': 'id name',
    'categories': 'id name kind',
    'receipts': 'id sha256 filename extension created_at status ocr_text error',
    'drafts': 'id receipt_id data status',
    'transactions': ('id kind amount_cents occurred_at category_id account_id to_account_id '
                     'purpose merchant reference platform receipt_id draft_id refund_of created_at '
                     'voided_at idempotency_key source_note time_precision'),
    'reconciliations': 'id day scope kind amount_cents note version updated_at',
    'rules': 'id keyword category_id field',
    'products': 'id name sku unit stock_milli minimum_milli created_at',
    'movements': 'id product_id kind delta_milli occurred_at note transaction_id created_at idempotency_key',
    'events': 'id entity entity_id action payload created_at',
    'menu_items': 'id name barcode price_cents stock_mode active created_at',
    'recipes': 'menu_id product_id quantity_milli',
    'orders': ('id number occurred_at total_cents account_id transaction_id payment_method '
               'payment_reference note status created_at idempotency_key'),
    'order_lines': 'id order_id menu_id name barcode price_cents quantity recipe_snapshot',
    'order_refunds': 'id order_id transaction_id reason occurred_at idempotency_key',
    'shop_settings': 'key value',
}
ROOT_FILES = {'ledger.sqlite3', 'README.txt', 'legacy-source.json', 'legacy-sources.json', MARKER}
DIRECTORIES = {'receipts', 'backups', 'backups/records'}


def _check_destination(destination):
    if not str(destination).strip():
        raise Problem('请选择明确的恢复目录')
    target = Path(destination).expanduser()
    if not target.is_absolute() or '..' in target.parts:
        raise Problem('恢复目录必须是明确的绝对路径，不能包含上级路径')
    for part in (target, *target.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise Problem('恢复目录及其父目录不能是符号链接或目录联接')
    if not target.parent.is_dir():
        raise Problem('恢复目录的上一级文件夹必须已存在')
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise Problem('恢复只允许新的或完全空的目录，拒绝覆盖已有账本或文件')
    return target


def _members(archive):
    members = archive.infolist()
    if len(members) > MAX_MEMBERS:
        raise Problem('备份文件数量超过恢复上限')
    seen, total, files = set(), 0, []
    for entry in members:
        name = entry.filename
        if entry.orig_filename != name or '\\' in name or name.startswith('/') or ':' in name:
            raise Problem('备份包含不安全的文件路径')
        parts = name.rstrip('/').split('/')
        if any(not p or p in {'.', '..'} or p.endswith((' ', '.')) for p in parts):
            raise Problem('备份包含不安全的文件路径')
        key = '/'.join(parts).casefold()
        if key in seen:
            raise Problem('备份包含重名或大小写冲突的文件')
        seen.add(key)
        mode = stat.S_IFMT(entry.external_attr >> 16)
        if mode not in {0, stat.S_IFREG, stat.S_IFDIR} or entry.external_attr & 0x400:
            raise Problem('备份包含链接或不支持的特殊文件')
        if entry.flag_bits & 1:
            raise Problem('不支持加密 ZIP，请选择本软件导出的完整备份')
        if entry.is_dir():
            if '/'.join(parts) not in DIRECTORIES:
                raise Problem('备份包含不属于账本的目录')
            continue
        if mode == stat.S_IFDIR:
            raise Problem('备份文件类型与路径不一致')
        allowed = name in ROOT_FILES or re.fullmatch(r'receipts/[0-9a-f]{64}\.(png|jpg|webp)', name)
        allowed = allowed or re.fullmatch(r'backups/records/[0-9a-f]{32}\.json', name)
        if not allowed:
            raise Problem('备份包含不支持的文件；登录凭证、程序及其他文件不能恢复')
        total += entry.file_size
        if entry.file_size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
            raise Problem('备份解压体积超过恢复上限')
        if entry.file_size > max(entry.compress_size, 1) * MAX_COMPRESSION_RATIO:
            raise Problem('备份压缩比例异常，拒绝解压')
        files.append(entry)
    if 'ledger.sqlite3' not in {entry.filename for entry in files}:
        raise Problem('备份缺少根目录 ledger.sqlite3，请选择本软件导出的 ZIP')
    return files, total


def _extract(archive, files, staging):
    for entry in files:
        path = staging.joinpath(*entry.filename.split('/'))
        path.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with archive.open(entry) as source, path.open('xb') as destination:
            while chunk := source.read(1024 * 1024):
                written += len(chunk)
                if written > entry.file_size or written > MAX_FILE_BYTES:
                    raise Problem('备份实际解压体积与声明不符')
                destination.write(chunk)
        if written != entry.file_size:
            raise Problem('备份文件不完整')


def _load_json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeError) as exc:
        raise Problem('备份中的 JSON 记录损坏：' + path.name) from exc


def _validate(staging):
    marker = staging / MARKER
    if marker.exists() and _load_json(marker) != SIGNATURE:
        raise Problem('测试账本标识无效，拒绝将其恢复为正式账本')
    for filename in ('legacy-source.json', 'legacy-sources.json'):
        if (staging / filename).exists():
            _load_json(staging / filename)
    uri = (staging / 'ledger.sqlite3').as_uri() + '?mode=ro&immutable=1'
    with closing(sqlite3.connect(uri, uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA trusted_schema=OFF')
        db.execute('PRAGMA query_only=ON')
        schema = db.execute("SELECT name,type,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
        tables = {row['name'] for row in schema if row['type'] == 'table'}
        if tables != set(REQUIRED_COLUMNS):
            raise Problem('数据库表结构与当前账本格式不兼容')
        if any(row['type'] in {'trigger', 'view'} or 'VIRTUAL TABLE' in (row['sql'] or '').upper() for row in schema):
            raise Problem('备份数据库包含不支持的触发器、视图或虚拟表')
        for table, columns in REQUIRED_COLUMNS.items():
            actual = {row['name'] for row in db.execute(f'PRAGMA table_info({table})')}
            if not set(columns.split()) <= actual:
                raise Problem('数据库缺少必要字段：' + table)
        metadata = dict(db.execute('SELECT key,value FROM metadata'))
        if metadata.get('schema_version') not in SUPPORTED_SCHEMA_VERSIONS:
            raise Problem('备份数据库版本不受支持，请使用匹配的软件版本恢复')
        if not metadata.get('ledger_id'):
            raise Problem('备份缺少账本标识')
        if [row[0] for row in db.execute('PRAGMA integrity_check')] != ['ok']:
            raise Problem('SQLite 数据库完整性检查失败')
        if db.execute('PRAGMA foreign_key_check').fetchone() is not None:
            raise Problem('备份存在缺失的关联记录，不能完整恢复')
        receipt_names = set()
        for receipt in db.execute('SELECT sha256,extension FROM receipts'):
            digest, extension = receipt['sha256'], receipt['extension']
            if not re.fullmatch('[0-9a-f]{64}', digest) or extension not in {'.png', '.jpg', '.webp'}:
                raise Problem('备份凭证文件信息无效')
            name = digest + extension
            receipt_names.add(name)
            path = staging / 'receipts' / name
            if not path.is_file():
                raise Problem('备份缺少原始凭证：' + name)
            with path.open('rb') as source:
                if hashlib.file_digest(source, 'sha256').hexdigest() != digest:
                    raise Problem('备份原始凭证校验失败：' + name)
        if {p.name for p in (staging / 'receipts').glob('*')} != receipt_names:
            raise Problem('备份包含无法关联到账本的凭证')
        event_names = set()
        for event in db.execute('SELECT * FROM events'):
            if not re.fullmatch('[0-9a-f]{32}', event['id']):
                raise Problem('备份事件标识无效')
            name = event['id'] + '.json'
            event_names.add(name)
            if _load_json(staging / 'backups' / 'records' / name) != dict(event):
                raise Problem('逐笔审计备份与数据库不一致：' + name)
            try:
                json.loads(event['payload'])
            except (ValueError, TypeError) as exc:
                raise Problem('数据库审计事件内容损坏') from exc
        if {p.name for p in (staging / 'backups' / 'records').glob('*')} != event_names:
            raise Problem('备份包含无法关联到账本的审计记录')
        counts = {table: db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                  for table in ('transactions', 'orders', 'products', 'receipts', 'events')}
    for folder in ('receipts', 'backups/records', 'backups/receipts', 'backups/database'):
        (staging / folder).mkdir(parents=True, exist_ok=True)
    for name in receipt_names:
        shutil.copyfile(staging / 'receipts' / name, staging / 'backups' / 'receipts' / name)
    return {'ledger_id': metadata['ledger_id'], 'test_mode': marker.exists(), 'counts': counts}


def restore_backup(zip_path, destination):
    """Restore a current exported ZIP into an empty absolute directory.

    Return ``destination``, ``ledger_id``, ``test_mode``, ``counts``, ``files`` and
    ``bytes``. No application is started and no login credentials are imported.
    Validation failures raise Problem and leave the destination absent or empty.
    Limits apply to archive size, expanded bytes, member count and compression.
    """
    try:
        target = _check_destination(destination)
        source = Path(zip_path).expanduser()
        if not source.is_file() or source.stat().st_size > MAX_TOTAL_BYTES:
            raise Problem('请选择不超过 2 GiB 的账本 ZIP 备份文件')
        with zipfile.ZipFile(source) as archive:
            files, total = _members(archive)
            if shutil.disk_usage(target.parent).free < total * 2 + 1024 * 1024:
                raise Problem('恢复目录可用空间不足，请换一个位置')
            with tempfile.TemporaryDirectory(prefix='.ledger-restore-', dir=target.parent) as directory:
                staging = Path(directory)
                _extract(archive, files, staging)
                result = _validate(staging)
                _check_destination(target)
                if target.exists():
                    target.rmdir()  # Fails if another writer populated it during validation.
                os.rename(staging, target)
        return {**result, 'destination': str(target), 'files': len(files), 'bytes': total}
    except Problem:
        raise
    except (OSError, ValueError, sqlite3.Error, zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
        raise Problem('备份恢复失败，未覆盖已有账本：' + str(exc)) from exc
