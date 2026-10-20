"""Create a persistent synthetic trial ledger without touching existing data."""
from datetime import date
import json
import os
from pathlib import Path
import tempfile

from .store import Problem, Store


MARKER = '.test-ledger.json'
SIGNATURE = {'kind': 'local-ledger-synthetic-trial', 'version': 1}


def prepare_trial(directory):
    root = Path(directory).resolve()
    if root.exists() and not root.is_dir():
        raise Problem('测试数据位置必须是新的空目录或已标记的测试目录')
    if root.exists() and any(root.iterdir()):
        try:
            marked = json.loads((root / MARKER).read_text(encoding='utf-8')) == SIGNATURE
        except (OSError, ValueError):
            marked = False
        if not marked or not (root / 'ledger.sqlite3').is_file():
            raise Problem('测试版拒绝打开已有的非测试目录，请使用新的空目录；正式账本不会被改动')
        return root
    root.parent.mkdir(parents=True, exist_ok=True)
    # Seed elsewhere, then publish the complete directory; interruption cannot
    # leave a half-seeded ledger which looks ready to use.
    with tempfile.TemporaryDirectory(prefix='.trial-setup-', dir=root.parent) as staging:
        store = Store(staging)
        day = date.today().isoformat()
        store.save_shop({'name': '测试餐饮店（模拟）', 'footer': '测试小票，无真实扣款'})
        for key, amount, purpose, category, kind in [
            ('purchase', '56.40', '测试食材采购', 'ingredients', 'expense'),
            ('seasoning', '12.00', '测试调料采购', 'purchase', 'expense'),
            ('income', '120.00', '测试营业收入', 'sales', 'income'),
        ]:
            store.add_transaction({'kind': kind, 'amount': amount, 'purpose': purpose,
                                   'category_id': category, 'account_id': 'cash',
                                   'occurred_at': day + ' 09:00:00', 'idempotency_key': 'trial-seed-' + key})
        for sku, name, quantity, minimum, dish, price, consumption in [
            ('RAW-001', '测试鸡肉', '4.2', '2', '测试宫保鸡丁', '18.00', '0.2'),
            ('RAW-002', '测试番茄', '2.55', '3', '测试番茄炒蛋', '12.00', '0.15'),
            ('RAW-003', '测试大米', '1.4', '1', '测试米饭', '2.00', '0.1'),
        ]:
            product = store.add_product({'sku': sku, 'name': name, 'unit': '千克', 'minimum': minimum})['id']
            store.add_movement({'product_id': product, 'kind': 'in', 'quantity': quantity,
                                'occurred_at': day + ' 09:00:00', 'idempotency_key': 'trial-seed-' + sku})
            store.save_menu({'name': dish, 'barcode': str(1001 + int(sku[-1]) - 1), 'price': price,
                             'stock_mode': 'recipe', 'recipe': [{'product_id': product, 'quantity': consumption}]})
        (Path(staging) / MARKER).write_text(json.dumps(SIGNATURE), encoding='utf-8')
        if root.exists():
            root.rmdir()  # Refuse to replace a directory if another process added files.
        os.rename(staging, root)
    return root
