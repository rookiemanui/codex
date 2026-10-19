from concurrent.futures import ThreadPoolExecutor
import json
import tempfile
import unittest
from uuid import uuid4

from localledger.store import Store, Problem
from localledger.access import set_password
from localledger.server import Application, make_server


class RestaurantCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.raw = self.store.add_product({'name': '鸡肉', 'unit': '千克'})['id']
        self.store.add_movement({'product_id': self.raw, 'kind': 'in', 'quantity': '5',
            'occurred_at': '2026-10-19 10:00', 'idempotency_key': uuid4().hex})
        self.dish = self.store.save_menu({'name': '猪肚鸡', 'barcode': '10001', 'price': '38.50',
            'stock_mode': 'recipe', 'recipe': [{'product_id': self.raw, 'quantity': '0.4'}]})['id']

    def tearDown(self):
        self.temp.cleanup()

    def sale(self, **overrides):
        return self.store.checkout({'items': [{'menu_id': self.dish, 'quantity': 2, 'expected_price_cents': 3850}],
            'expected_total_cents': 7700, 'payment_method': 'cash', 'payment_confirmed': True,
            'account_id': 'cash', 'occurred_at': '2026-10-19 12:00', 'idempotency_key': uuid4().hex, **overrides})

    def test_sale_commits_cash_stock_snapshots_and_backups(self):
        result = self.sale()
        self.assertTrue(result['backup_ok'])
        self.assertEqual(self.store.summary('2026-10')['income_cents'], 7700)
        self.assertEqual(self.store.products()[0]['stock_milli'], 4200)
        order = self.store.order(result['id'])
        self.assertEqual(order['lines'][0]['quantity'], 2)
        self.assertEqual(json.loads(order['lines'][0]['recipe_snapshot'])[0]['quantity_milli'], 400)
        self.assertEqual(len(self.store.movements()), 2)
        self.assertEqual(self.store.backup_status()['event_count'], self.store.backup_status()['record_backup_count'])

    def test_retry_and_concurrent_submit_do_not_double_charge_ledger_or_stock(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.sale(idempotency_key='same-sale'), range(2)))
        self.assertEqual(results[0]['id'], results[1]['id'])
        self.assertEqual(len(self.store.orders()), 1)
        self.assertEqual(self.store.products()[0]['stock_milli'], 4200)

    def test_insufficient_stock_rolls_back_whole_sale(self):
        with self.assertRaises(Problem):
            self.sale(items=[{'menu_id': self.dish, 'quantity': 20, 'expected_price_cents': 3850}], expected_total_cents=77000)
        self.assertEqual(self.store.orders(), [])
        self.assertEqual(self.store.transactions(), [])
        self.assertEqual(self.store.products()[0]['stock_milli'], 5000)

    def test_changed_price_and_unconfirmed_payment_are_rejected(self):
        for override in [{'payment_confirmed': False}, {'expected_total_cents': 123},
                         {'items': [{'menu_id': self.dish, 'quantity': 2, 'expected_price_cents': 3800}]}]:
            with self.subTest(override=override), self.assertRaises(Problem):
                self.sale(**override)
        self.assertEqual(self.store.transactions(), [])

    def test_old_receipt_retains_price_name_and_recipe_after_menu_edit(self):
        result = self.sale()
        self.store.save_menu({'name': '新名称', 'barcode': '10001', 'price': '40', 'stock_mode': 'none', 'recipe': []}, self.dish)
        row = self.store.order(result['id'])['lines'][0]
        self.assertEqual((row['name'], row['price_cents']), ('猪肚鸡', 3850))
        self.assertTrue(json.loads(row['recipe_snapshot']))

    def test_whole_refund_reduces_income_without_restocking_or_deleting(self):
        sale = self.sale()
        tx = self.store.order(sale['id'])['transaction_id']
        with self.assertRaises(Problem):
            self.store.void(tx)
        payload = {'reason': '顾客退单', 'occurred_at': '2026-10-20 12:00', 'account_id': 'cash',
                   'payment_confirmed': True, 'idempotency_key': 'refund-1'}
        refund = self.store.refund_order(sale['id'], payload)
        again = self.store.refund_order(sale['id'], payload)
        self.assertEqual(refund['id'], again['id'])
        summary = self.store.summary('2026-10')
        self.assertEqual((summary['income_cents'], summary['expense_cents']), (0, 0))
        self.assertEqual(self.store.products()[0]['stock_milli'], 4200)
        self.assertEqual(self.store.order(sale['id'])['status'], 'refunded')
        refund_tx = self.store.order(sale['id'])['refund']['transaction_id']
        with self.assertRaises(Problem):
            self.store.void(refund_tx)
        with self.assertRaises(Problem):
            self.store.refund_order(sale['id'], {**payload, 'idempotency_key': 'refund-2'})

    def test_duplicate_payment_reference_rolls_back_stock(self):
        self.sale(payment_reference='unique-reference')
        with self.assertRaises(Problem):
            self.sale(payment_reference='unique-reference')
        self.assertEqual(self.store.products()[0]['stock_milli'], 4200)

    def test_recipe_unit_precision_and_duplicate_ingredients_are_rejected(self):
        for recipe in [[{'product_id': self.raw, 'quantity': '0.0005'}],
                       [{'product_id': self.raw, 'quantity': '0.1'}, {'product_id': self.raw, 'quantity': '0.2'}]]:
            with self.assertRaises(Problem):
                self.store.save_menu({'name': '测试', 'barcode': uuid4().hex, 'price': 1, 'stock_mode': 'recipe', 'recipe': recipe})

    def test_competing_orders_cannot_oversell_shared_ingredient(self):
        self.store.add_movement({'product_id': self.raw, 'kind': 'adjust', 'quantity': '1',
            'occurred_at': '2026-10-19 11:00', 'idempotency_key': uuid4().hex})
        def attempt(_):
            try:
                return self.sale()
            except Problem:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, range(2)))
        self.assertEqual(sum(x is not None for x in results), 1)
        self.assertEqual(self.store.products()[0]['stock_milli'], 200)
        self.assertEqual(self.store.summary('2026-10')['income_cents'], 7700)

    def test_sales_refund_in_next_month_keeps_previous_month_sales(self):
        key = self.sale()['id']
        self.store.refund_order(key, {'reason': '跨月退单', 'occurred_at': '2026-11-01 12:00',
            'payment_confirmed': True, 'account_id': 'cash', 'idempotency_key': uuid4().hex})
        self.assertEqual(self.store.summary('2026-10')['income_cents'], 7700)
        later = self.store.summary('2026-11')
        self.assertEqual((later['income_cents'], later['expense_cents']), (-7700, 0))
        self.assertEqual(later['days'][0]['income_cents'], -7700)

    def test_password_required_and_public_listener_restricted(self):
        with self.assertRaises(Problem):
            Application(self.temp.name, public_url='https://shop.example.com')
        set_password(self.temp.name, 'test-password-123456')
        app = Application(self.temp.name, public_url='https://shop.example.com')
        try:
            with self.assertRaises(Problem):
                make_server(app, '0.0.0.0', 0)
            with self.assertRaises(Problem):
                app.login('123456', 'test')
            self.assertTrue(app.login('test-password-123456', 'test'))
        finally:
            app.close()


if __name__ == '__main__':
    unittest.main()
