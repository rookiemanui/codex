#!/usr/bin/env python3
"""Explicit, read-only migration from a Bookkeeping SQLite database.

The original database is never modified. No original images are invented.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from localledger.store import Store, Problem, money


def migrate(source, destination):
    source = Path(source).resolve()
    if not source.is_file():
        raise Problem('找不到原账本文件')
    store = Store(destination)
    if source == store.db_path:
        raise Problem('来源账本与新账本不能是同一文件')
    with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = [dict(x) for x in db.execute('SELECT * FROM entries ORDER BY id')]
    if any(row['type'] not in ['支出', '收入'] for row in rows):
        raise Problem('此迁移器仅支持已确认的收入和支出；转账、退款等须另行核对后迁移')
    if any(row['status'] != 'confirmed' for row in rows):
        raise Problem('原账本有未确认用途，请先完成分类')
    source_fingerprint = hashlib.sha256(source.read_bytes()).hexdigest()
    registry_path = store.root / 'legacy-sources.json'
    registry = json.loads(registry_path.read_text()) if registry_path.exists() else {}
    source_key = str(source)
    if source_key not in registry:
        registry[source_key] = source_fingerprint[:24]
        registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding='utf-8')
    source_identity = registry[source_key]
    options = store.options()
    accounts = {row['name']: row['id'] for row in options['accounts']}
    categories = {row['name']: row['id'] for row in options['categories']}
    mapping = {'交通/公共交通': 'transport', '娱乐/网游电玩': 'leisure', '餐饮/买菜原料': 'ingredients'}
    imported, skipped = 0, 0
    for row in rows:
        kind = 'expense' if row['type'] == '支出' else 'income'
        if row['account'] not in accounts:
            accounts[row['account']] = store.add_account({'name': row['account']})['id']
        label = row['category'] or ('历史支出' if kind == 'expense' else '历史收入')
        category = mapping.get(label) or categories.get(label)
        if not category:
            category = store.add_category({'name': label, 'kind': kind})['id']
            categories[label] = category
        note = row['note'] or ''
        purpose_match = re.search(r'用户补充确认用途：([^；]+)', note)
        purpose = purpose_match.group(1) if purpose_match else '公交乘车' if label == '交通/公共交通' else row['transaction_object'] or label
        ref = re.search(r'交易单号=([0-9]+)', note)
        precision = 'minute' if '分钟' in note else 'second'
        value = {'kind': kind, 'amount': money(row['amount_cents']), 'occurred_at': row['transaction_time'],
                 'account_id': accounts[row['account']], 'category_id': category, 'purpose': purpose,
                 'merchant': row['transaction_object'] or '', 'reference': ref.group(1) if ref else '',
                 'platform': '微信' if '微信零钱' == row['account'] else '',
                 'source_note': '历史来源记录：' + note, 'time_precision': precision,
                 'idempotency_key': 'legacy-' + source_identity + '-' + str(row['id'])}
        result = store.add_transaction(value)
        if result['already_saved']:
            store.annotate_source(result['id'], value['source_note'], precision)
            skipped += 1
        else:
            imported += 1
    provenance = store.root / 'legacy-source.json'
    provenance.write_text(json.dumps({'source_filename': source.name, 'source_sha256': source_fingerprint,
                                      'original_images_imported': False, 'source_rows': rows}, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'imported': imported, 'already_imported': skipped,
            'original_images_imported': False, 'source_unchanged': hashlib.sha256(source.read_bytes()).hexdigest() == source_fingerprint}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('source', help='原 Bookkeeping SQLite 文件')
    parser.add_argument('--data-dir', required=True, help='本软件的数据文件夹')
    args = parser.parse_args()
    print(json.dumps(migrate(args.source, args.data_dir), ensure_ascii=False, indent=2))
