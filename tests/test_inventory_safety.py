from contextlib import closing
from copy import deepcopy
import http.cookiejar
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
from uuid import uuid4

from localledger.server import Application, make_server
from localledger.store import Problem, Store


class InventorySafetyCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = Store(self.temporary.name)
        self.product = self.store.add_product({'name': '鸡肉', 'unit': '千克'})['id']

    def tearDown(self):
        self.temporary.cleanup()

    def transaction(self, kind='expense', amount='100', **extra):
        return {'kind': kind, 'amount': amount, 'category_id': 'ingredients' if kind == 'expense' else 'sales',
                'account_id': 'cash', 'occurred_at': '2026-10-22 10:00',
                'idempotency_key': uuid4().hex, **extra}

    def movement(self, kind='in', quantity='10', **extra):
        return {'product_id': self.product, 'kind': kind, 'quantity': quantity,
                'occurred_at': '2026-10-22 10:00', 'idempotency_key': uuid4().hex, **extra}

    def reverse(self, key, **extra):
        return self.store.reverse_movement(key, {'reason': '采购数量录错', 'idempotency_key': uuid4().hex, **extra})

    def test_invalid_movement_finance_combinations_leave_stock_and_money_unchanged(self):
        self.store.add_movement(self.movement())
        version = self.store.products()[0]['stock_version']
        for kind, finance in [('in', 'income'), ('out', 'expense'), ('loss', 'income'),
                              ('customer_return', 'expense'), ('supplier_return', 'income'), ('adjust', 'expense')]:
            with self.subTest(kind=kind), self.assertRaises(Problem):
                self.store.add_movement(self.movement(kind, '2', expected_stock_version=version,
                                                       transaction=self.transaction(finance)))
        self.assertEqual(self.store.products()[0]['stock_milli'], 10000)
        self.assertEqual(len(self.store.movements()), 1)
        self.assertEqual(self.store.transactions(), [])

    def test_supplier_return_refund_reduces_purchase_expense_atomically(self):
        purchase = self.store.add_movement(self.movement(transaction=self.transaction()))
        self.store.add_movement(self.movement('supplier_return', '2',
            transaction=self.transaction('refund', '20', refund_of=purchase['transaction_id'])))
        summary = self.store.summary('2026-10')
        self.assertEqual((summary['income_cents'], summary['expense_cents']), (0, 8000))
        self.assertEqual(self.store.products()[0]['stock_milli'], 8000)
        other = self.store.add_product({'name': '小酥肉'})['id']
        self.store.add_movement(self.movement(product_id=other))
        with self.assertRaisesRegex(Problem, '此原料'):
            self.store.add_movement(self.movement('supplier_return', '1', product_id=other,
                transaction=self.transaction('refund', '10', refund_of=purchase['transaction_id'])))

    def test_stale_count_rejects_concurrent_purchase_even_if_stock_returns_to_same_value(self):
        self.store.add_movement(self.movement())
        version = self.store.products()[0]['stock_version']
        self.store.add_movement(self.movement(quantity='5'))
        count = self.movement('adjust', '8', expected_stock_version=version)
        with self.assertRaisesRegex(Problem, '已变更库存'):
            self.store.add_movement(count)
        self.assertEqual(self.store.products()[0]['stock_milli'], 15000)
        self.store.add_movement(self.movement('out', '5'))
        with self.assertRaises(Problem):
            self.store.add_movement(count)
        count['expected_stock_version'] = self.store.products()[0]['stock_version']
        first = self.store.add_movement(count)
        retry = self.store.add_movement(count)
        self.assertEqual(first['id'], retry['id'])
        self.assertEqual(self.store.products()[0]['stock_milli'], 8000)
        with self.assertRaisesRegex(Problem, '缺少盘点'):
            self.store.add_movement(self.movement('adjust', '7'))

    def test_lost_response_retry_survives_restart_and_rejects_changed_payload(self):
        request = self.movement(transaction=self.transaction())
        first = self.store.add_movement(request)
        self.store = Store(self.temporary.name)
        self.assertEqual(self.store.add_movement(deepcopy(request))['id'], first['id'])
        for field, changed in [('quantity', '12'), ('note', 'different'), ('kind', 'out')]:
            altered = deepcopy(request)
            altered[field] = changed
            with self.subTest(field=field), self.assertRaisesRegex(Problem, '同一编号'):
                self.store.add_movement(altered)
        altered = deepcopy(request)
        altered['transaction']['amount'] = '120'
        with self.assertRaises(Problem):
            self.store.add_movement(altered)
        self.assertEqual(self.store.products()[0]['stock_milli'], 10000)
        self.assertEqual(self.store.summary('2026-10')['expense_cents'], 10000)

    def test_purchase_reversal_corrects_stock_and_money_preserves_history_and_retries(self):
        original = self.store.add_movement(self.movement(transaction=self.transaction()))
        with self.assertRaisesRegex(Problem, '库存管理'):
            self.store.void(original['transaction_id'])
        request = {'reason': '数量和金额录错', 'idempotency_key': 'reverse-once'}
        first = self.store.reverse_movement(original['id'], request)
        again = self.store.reverse_movement(original['id'], request)
        self.assertEqual(first['id'], again['id'])
        self.assertEqual(again['transaction_id'], original['transaction_id'])
        self.assertEqual(self.store.products()[0]['stock_milli'], 0)
        self.assertEqual(self.store.summary('2026-10')['expense_cents'], 0)
        self.assertTrue(self.store.transactions()[0]['voided_at'])
        moves = {m['id']: m for m in self.store.movements()}
        self.assertEqual(moves[original['id']]['reversed_by'], first['id'])
        self.assertEqual(moves[first['id']]['reversal_of'], original['id'])
        with self.assertRaises(Problem):
            self.reverse(original['id'])
        with self.assertRaises(Problem):
            self.reverse(first['id'])
        with self.assertRaises(Problem):
            self.store.reverse_movement(original['id'], {**request, 'reason': '不同原因'})
        self.store.add_movement(self.movement(quantity='12', transaction=self.transaction(amount='120')))
        self.assertEqual(self.store.products()[0]['stock_milli'], 12000)
        self.assertEqual(self.store.summary('2026-10')['expense_cents'], 12000)

    def test_reversal_cannot_make_stock_negative_or_bypass_linked_refund(self):
        original = self.store.add_movement(self.movement(transaction=self.transaction()))
        self.store.add_movement(self.movement('out', '2'))
        with self.assertRaisesRegex(Problem, '负库存'):
            self.reverse(original['id'])
        self.assertFalse(self.store.transactions()[0]['voided_at'])
        self.store.add_movement(self.movement(quantity='2'))
        self.store.add_transaction(self.transaction('refund', '10', refund_of=original['transaction_id']))
        with self.assertRaisesRegex(Problem, '存在退款'):
            self.reverse(original['id'])
        self.assertEqual(self.store.products()[0]['stock_milli'], 10000)

    def test_reversal_failure_rolls_back_both_stock_and_transaction(self):
        original = self.store.add_movement(self.movement(transaction=self.transaction()))
        with patch.object(self.store, '_event', side_effect=sqlite3.OperationalError('simulated disk failure')):
            with self.assertRaises(sqlite3.OperationalError):
                self.reverse(original['id'])
        self.assertEqual(self.store.products()[0]['stock_milli'], 10000)
        self.assertFalse(self.store.transactions()[0]['voided_at'])
        self.assertEqual(len(self.store.movements()), 1)

    def test_reversal_does_not_override_a_later_physical_stock_count(self):
        self.store.add_movement(self.movement())
        outgoing = self.store.add_movement(self.movement('out', '2'))
        self.store.add_movement(self.movement('adjust', '10',
            expected_stock_version=self.store.products()[0]['stock_version']))
        with self.assertRaisesRegex(Problem, '之后已有盘点'):
            self.reverse(outgoing['id'])
        self.assertEqual(self.store.products()[0]['stock_milli'], 10000)

    def test_pos_recipe_movement_cannot_be_reversed_as_manual_stock(self):
        self.store.add_movement(self.movement())
        menu = self.store.save_menu({'name': '小锅鸡', 'barcode': 'A1', 'price': '20',
            'stock_mode': 'recipe', 'recipe': [{'product_id': self.product, 'quantity': '0.5'}]})['id']
        sale = self.store.checkout({'items': [{'menu_id': menu, 'quantity': 1, 'expected_price_cents': 2000}],
            'expected_total_cents': 2000,
            'occurred_at': '2026-10-22 12:00', 'account_id': 'cash', 'payment_method': 'cash',
            'payment_confirmed': True, 'idempotency_key': uuid4().hex})
        order = self.store.order(sale['id'])
        movement = next(m for m in self.store.movements() if m['transaction_id'] == order['transaction_id'])
        with self.assertRaisesRegex(Problem, '收银账目'):
            self.reverse(movement['id'])
        with self.assertRaisesRegex(Problem, '销售订单'):
            self.store.add_transaction(self.transaction('sale_refund', '10', refund_of=order['transaction_id']))

    def test_manual_income_partial_refunds_and_later_month_keep_correct_totals(self):
        original = self.store.add_transaction(self.transaction('income', '100'))['id']
        request = self.transaction('sale_refund', '20', refund_of=original)
        first = self.store.add_transaction(request)
        self.assertEqual(first['id'], self.store.add_transaction(request)['id'])
        self.store.add_transaction(self.transaction('sale_refund', '30', refund_of=original,
            occurred_at='2026-11-01 11:00'))
        with self.assertRaisesRegex(Problem, '可退余额'):
            self.store.add_transaction(self.transaction('sale_refund', '51', refund_of=original))
        self.assertEqual(self.store.summary('2026-10')['income_cents'], 8000)
        self.assertEqual(self.store.summary('2026-11')['income_cents'], -3000)
        self.assertEqual(self.store.summary('2026-11')['expense_cents'], 0)

    def test_backup_database_handles_close_before_replace_and_export_restore(self):
        self.store.add_movement(self.movement())
        self.store.add_movement(self.movement(quantity='1'))
        self.assertTrue(self.store.backup_status()['database_backups'])
        snapshot = next((Path(self.temporary.name) / 'backups/database').glob('*.sqlite3'))
        renamed = snapshot.with_suffix('.checked')
        snapshot.rename(renamed)
        renamed.rename(snapshot)
        archive = self.store.backup_file()
        exported = Path(self.temporary.name) / 'backups/database/export.sqlite3'
        with closing(sqlite3.connect(exported)) as db:
            self.assertEqual(db.execute('SELECT stock_milli FROM products').fetchone()[0], 11000)
        exported.unlink()
        archive.unlink()

    def test_reverse_http_endpoint_requires_auth_and_returns_audited_result(self):
        app = Application(self.temporary.name, code='123456')
        server = make_server(app, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f'http://127.0.0.1:{server.server_address[1]}'
        client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        def call(path, data=None, csrf=''):
            request = urllib.request.Request(url + path, data=json.dumps(data).encode() if data is not None else None,
                headers={'Content-Type': 'application/json', 'X-Local-CSRF': csrf})
            return client.open(request, timeout=10)
        try:
            movement = app.store.add_movement(self.movement())
            path = '/api/movements/' + movement['id'] + '/reverse'
            request = {'reason': 'HTTP测试撤销', 'idempotency_key': 'http-reverse'}
            with self.assertRaises(urllib.error.HTTPError) as error:
                call(path, request)
            self.assertEqual(error.exception.code, 401)
            with call('/api/status') as response:
                csrf = json.load(response)['csrf']
            with self.assertRaises(urllib.error.HTTPError) as error:
                call(path, request)
            self.assertEqual(error.exception.code, 403)
            with call(path, request, csrf) as response:
                result = json.load(response)
                self.assertTrue(result['backup_ok'])
                self.assertEqual(result['reversed_movement_id'], movement['id'])
            self.assertEqual(app.store.products()[0]['stock_milli'], 0)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
            app.close()
