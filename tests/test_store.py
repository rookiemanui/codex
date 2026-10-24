import base64
import csv
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
import unittest
from uuid import uuid4
import zipfile
import io

from localledger.store import Store, Problem, csv_cell, scaled, quantity
from localledger.recognition import parse_payment


PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jN1sAAAAASUVORK5CYII=')


class LedgerCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = Store(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def entry(self, amount='10.00', kind='expense', **kwargs):
        value = {'amount': amount, 'kind': kind, 'occurred_at': '2026-10-06 13:40:21',
                 'category_id': 'ingredients' if kind == 'expense' else 'sales', 'account_id': 'cash',
                 'idempotency_key': uuid4().hex, 'purpose': '测试用途', **kwargs}
        return self.store.add_transaction(value)

    def movement(self, product, kind, quantity, **extra):
        return self.store.add_movement({'product_id': product, 'kind': kind, 'quantity': quantity,
                                       'occurred_at': '2026-10-06 13:40', 'idempotency_key': uuid4().hex, **extra})

    def test_inventory_display_preserves_integer_trailing_zeros(self):
        self.assertEqual([quantity(x) for x in [0, 10000, 100000, 1500, 10]], ['0', '10', '100', '1.5', '0.01'])

    def test_inventory_sheet_has_exact_quantities_and_safe_text(self):
        key = self.store.add_product({'name': '=HYPERLINK("unsafe")', 'sku': '@SUM(1,1)',
                                      'unit': '千克', 'minimum': '1.5'})['id']
        self.movement(key, 'in', '1.25')
        rows = list(csv.reader(io.StringIO(self.store.export_inventory_csv().decode('utf-8-sig'))))
        self.assertEqual(rows[0][-2:], ['实盘数量（手填）', '备注（手填）'])
        self.assertEqual(rows[1], ["'@SUM(1,1)", "'=HYPERLINK(\"unsafe\")", '千克',
                                   '1.25', '1.5', '待补货', '', ''])
        self.assertEqual(csv_cell(' \t=HYPERLINK("unsafe")'), "' \t=HYPERLINK(\"unsafe\")")

    def test_money_rejects_rounding_and_non_finite_values(self):
        for value in ['1.005', 'NaN', 'Infinity', '-1', '0', '', 'abc']:
            with self.subTest(value=value), self.assertRaises(Problem):
                scaled(value)
        self.assertEqual(scaled('16.80'), 1680)

    def test_minute_precision_is_not_presented_as_known_seconds(self):
        self.entry(occurred_at='2026-10-06 13:40', source_note='通知只显示分钟')
        row = self.store.transactions()[0]
        self.assertEqual(row['time_precision'], 'minute')
        self.assertEqual(row['source_note'], '通知只显示分钟')

    def test_old_database_upgrade_preserves_amount_and_unknown_precision(self):
        self.entry()
        with closing(sqlite3.connect(self.store.db_path)) as db, db:
            db.execute('ALTER TABLE transactions DROP COLUMN source_note')
            db.execute('ALTER TABLE transactions DROP COLUMN time_precision')
        upgraded = Store(self.root)
        row = upgraded.transactions()[0]
        self.assertEqual(row['amount_cents'], 1000)
        self.assertEqual(row['time_precision'], 'unknown')

    def test_same_amount_nearby_different_transactions_are_retained(self):
        self.entry(merchant='甲商户', occurred_at='2026-10-06 13:40:21')
        self.entry(merchant='乙商户', occurred_at='2026-10-06 13:40:22')
        self.assertEqual(len(self.store.transactions()), 2)
        self.assertEqual(self.store.summary('2026-10')['expense_cents'], 2000)

    def test_same_second_equal_amount_is_not_evidence_of_duplicate(self):
        self.entry(merchant='甲商户')
        self.entry(merchant='乙商户')
        self.assertEqual(len(self.store.transactions()), 2)

    def test_retry_is_idempotent(self):
        first = self.entry(idempotency_key='same-request')
        second = self.entry(idempotency_key='same-request')
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(len(self.store.transactions()), 1)

    def test_reference_duplicate_is_rejected(self):
        self.entry(platform='测试支付', reference='TX-1')
        with self.assertRaises(Problem):
            self.entry(platform='测试支付', reference='TX-1')
        self.assertEqual(len(self.store.transactions()), 1)

    def test_user_sample_totals_and_classification(self):
        for amount, category in [('2.00', 'transport'), ('6.90', 'leisure'), ('2.00', 'transport'),
                                 ('16.80', 'ingredients'), ('9.26', 'ingredients')]:
            self.entry(amount, category_id=category)
        summary = self.store.summary('2026-10')
        self.assertEqual(summary['expense_cents'], 3696)
        self.assertEqual({c['name']: c['amount_cents'] for c in summary['categories']},
                         {'交通出行': 400, '休闲娱乐': 690, '餐饮食材': 2606})

    def test_refund_reduces_expense_not_income(self):
        original = self.entry('16.80')['id']
        self.entry('6.80', 'refund', refund_of=original)
        summary = self.store.summary('2026-10')
        self.assertEqual((summary['expense_cents'], summary['income_cents']), (1000, 0))
        self.assertEqual(summary['categories'], [{'name': '餐饮食材', 'amount_cents': 1000}])

    def test_partial_refund_cannot_exceed_remaining(self):
        original = self.entry('10.00')['id']
        self.entry('4', 'refund', refund_of=original)
        self.entry('5', 'refund', refund_of=original)
        with self.assertRaises(Problem):
            self.entry('2', 'refund', refund_of=original)
        self.assertEqual(len(self.store.transactions()), 3)

    def test_refund_in_later_month_uses_original_category(self):
        original = self.entry('10')['id']
        self.entry('3', 'refund', refund_of=original, occurred_at='2026-11-01 12:00')
        self.assertEqual(self.store.summary('2026-11')['expense_cents'], -300)
        self.assertEqual(self.store.summary('2026-10')['expense_cents'], 1000)

    def test_internal_transfer_is_neither_income_nor_expense(self):
        self.entry('100', 'transfer', to_account_id='wechat')
        summary = self.store.summary('2026-10')
        self.assertEqual((summary['income_cents'], summary['expense_cents']), (0, 0))
        with self.assertRaises(Problem):
            self.entry('100', 'transfer', to_account_id='cash')

    def test_reclassification_also_updates_linked_refund(self):
        original = self.entry('10')['id']
        self.entry('3', 'refund', refund_of=original)
        self.store.reclassify(original, {'category_id': 'supplies'})
        self.assertEqual(self.store.summary('2026-10')['categories'], [{'name': '日常杂费', 'amount_cents': 700}])

    def test_generic_platform_rule_is_rejected_atomically(self):
        with self.assertRaises(Problem):
            self.entry(learn_keyword='淘宝')
        self.assertEqual(self.store.transactions(), [])
        self.entry(learn_keyword='猪肚鸡调料')
        self.assertEqual(self.store.rules()[0]['keyword'], '猪肚鸡调料')

    def test_void_preserves_record_and_history(self):
        original = self.entry()['id']
        self.store.void(original)
        self.assertTrue(self.store.transactions()[0]['voided_at'])
        self.assertEqual(self.store.summary('2026-10')['expense_cents'], 0)
        self.assertEqual(len(list((self.root/'backups/records').glob('*.json'))), 2)

    def test_parent_cannot_be_voided_before_refund(self):
        original = self.entry()['id']
        self.entry('1', 'refund', refund_of=original)
        with self.assertRaises(Problem):
            self.store.void(original)

    def test_image_stored_twice_and_same_file_not_reimported(self):
        first = self.store.save_receipt('凭证.png', PNG)
        second = self.store.save_receipt('另一个文件名.png', PNG)
        self.assertTrue(first['backup_ok'])
        self.assertTrue(second['duplicate_file'])
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(self.store.receipt_path(first['id']).read_bytes(), PNG)
        self.assertEqual(next((self.root/'backups/receipts').iterdir()).read_bytes(), PNG)

    def test_one_image_can_back_multiple_distinct_transactions(self):
        key = self.store.save_receipt('multi.png', PNG)['id']
        self.store.set_ocr(key, '原始识别文本', [{'amount': '2.00'}, {'amount': '2.00'}])
        drafts = self.store.receipt(key)['drafts']
        for draft in drafts:
            self.entry('2', draft_id=draft['id'])
        self.assertEqual(len(self.store.transactions()), 2)
        with self.assertRaises(Problem):
            self.entry('2', draft_id=drafts[0]['id'])

    def test_stock_uses_integer_thousandths_and_prevents_negative_stock(self):
        key = self.store.add_product({'name': '青菜', 'unit': '千克', 'minimum': '1'})['id']
        self.movement(key, 'in', '2.500')
        self.movement(key, 'out', '0.375')
        self.assertEqual(self.store.products()[0]['stock_milli'], 2125)
        with self.assertRaises(Problem):
            self.movement(key, 'out', '3')
        self.assertEqual(len(self.store.movements()), 2)

    def test_stock_request_retry_does_not_double_deduct(self):
        key = self.store.add_product({'name': '饮料'})['id']
        self.movement(key, 'in', '10')
        self.movement(key, 'out', '1', idempotency_key='same-stock-request')
        self.movement(key, 'out', '1', idempotency_key='same-stock-request')
        self.assertEqual(self.store.products()[0]['stock_milli'], 9000)

    def test_stock_and_finance_are_atomic(self):
        key = self.store.add_product({'name': '调料'})['id']
        self.movement(key, 'in', '10')
        with self.assertRaises(Problem):
            self.movement(key, 'out', '2', transaction={'kind': 'income', 'amount': '20', 'account_id': 'cash',
                                                     'category_id': 'ingredients', 'occurred_at': '2026-10-06 14:00',
                                                     'idempotency_key': uuid4().hex})
        self.assertEqual(self.store.products()[0]['stock_milli'], 10000)
        self.assertEqual(len(self.store.movements()), 1)
        self.assertEqual(self.store.transactions(), [])

    def test_finance_can_be_linked_to_stock(self):
        key = self.store.add_product({'name': '调料'})['id']
        result = self.movement(key, 'in', '5', transaction={'kind': 'expense', 'amount': '16.80', 'account_id': 'cash',
                                                          'category_id': 'purchase', 'occurred_at': '2026-10-06 14:00',
                                                          'idempotency_key': uuid4().hex})
        self.assertTrue(result['transaction_id'])
        self.assertEqual(self.store.summary('2026-10')['expense_cents'], 1680)

    def test_backup_contains_actual_original_and_restorable_database(self):
        key = self.store.save_receipt('receipt.png', PNG)['id']
        self.entry(receipt_id=key)
        bundle = self.store.export_backup()
        with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
            self.assertIsNone(archive.testzip())
            originals = [n for n in archive.namelist() if n.startswith('receipts/')]
            self.assertEqual(archive.read(originals[0]), PNG)
            destination = self.root/'restore'
            archive.extractall(destination)
        restored = Store(destination)
        self.assertEqual(len(restored.transactions()), 1)
        self.assertEqual(restored.receipt_path(key).read_bytes(), PNG)
        self.assertEqual((destination / 'backups/receipts' / restored.receipt_path(key).name).read_bytes(), PNG)

    def test_corrupt_receipt_or_backup_is_repaired_from_intact_partner(self):
        key = self.store.save_receipt('receipt.png', PNG)['id']
        original = self.store.receipt_path(key)
        backup = self.root / 'backups/receipts' / original.name
        original.write_bytes(b'corrupted-original')
        self.store.recover_backups()
        self.assertEqual(original.read_bytes(), PNG)
        backup.write_bytes(b'corrupted-backup')
        self.store.recover_backups()
        self.assertEqual(backup.read_bytes(), PNG)

    def test_no_valid_receipt_prevents_claiming_complete_export(self):
        key = self.store.save_receipt('receipt.png', PNG)['id']
        original = self.store.receipt_path(key)
        backup = self.root / 'backups/receipts' / original.name
        original.unlink()
        backup.unlink()
        with self.assertRaisesRegex(Problem, '均缺失或损坏'):
            self.store.export_backup()
        self.assertTrue(self.store.receipt(key))

    def test_missing_record_backup_is_recovered_from_database_events(self):
        self.entry()
        event = next((self.root/'backups/records').glob('*.json'))
        event.unlink()
        Store(self.root)
        self.assertTrue(event.exists())

    def test_corrupted_record_backup_is_repaired_from_event_outbox(self):
        self.entry()
        event = next((self.root/'backups/records').glob('*.json'))
        expected = event.read_text(encoding='utf-8')
        event.write_text('{broken', encoding='utf-8')
        self.store.recover_backups()
        self.assertEqual(event.read_text(encoding='utf-8'), expected)

    def test_concurrent_phone_and_computer_writes_all_have_backups(self):
        with ThreadPoolExecutor(max_workers=4) as workers:
            results = list(workers.map(lambda i: self.entry(str(i+1), purpose=f'并发账目{i}'), range(20)))
        self.assertTrue(all(x['backup_ok'] for x in results))
        self.assertEqual(len(self.store.transactions()), 20)
        self.assertEqual(self.store.backup_status()['record_backup_count'], 20)

    def test_csv_cannot_execute_user_text_as_formula(self):
        self.entry(purpose='=HYPERLINK("https://example.invalid")')
        self.assertIn("'=HYPERLINK", self.store.export_csv().decode('utf-8-sig'))


class ParsingCase(unittest.TestCase):
    def test_labeled_amount_is_not_confused_with_account_balance(self):
        draft = parse_payment(['交易金额：￥12.30', '商户：模拟餐馆',
                               '交易时间：2026-10-18 12:00:00', '账户余额', '￥888.88'])[0]
        self.assertEqual(draft['amount'], '12.30')
        self.assertEqual(draft['merchant'], '模拟餐馆')
        self.assertEqual(draft['occurred_at'], '2026-10-18 12:00:00')

    def test_multi_payment_image_keeps_both_equal_bus_fares(self):
        lines = ['公交商户', '10月6日19:54', '付款成功', '￥2.00', '付款方式', '储蓄卡(1234)',
                 '商品说明', '公交支撑平台-BRT2号线', '乘车时间', '2026-10-06 19:53:55',
                 '抖音生活服务商家', '10月6日14:59', '付款成功', '￥6.90', '付款方式', '储蓄卡(1234)',
                 '公交商户', '10月6日13:40', '付款成功', '￥2.00', '商品说明', '公交支撑平台-BRT2号线',
                 '乘车时间', '2026-10-06 13:40:21']
        drafts = parse_payment(lines)
        self.assertEqual([d['amount'] for d in drafts], ['2.00', '6.90', '2.00'])
        self.assertEqual([d['category_id'] for d in drafts], ['transport', '', 'transport'])
        self.assertEqual(drafts[0]['occurred_at'], '2026-10-06 19:54:00')
        self.assertEqual(drafts[1]['merchant'], '抖音生活服务商家')

    def test_status_bar_duplicate_and_reward_points_not_double_counted(self):
        lines = [{'text': '-16.80', 'y': 30}, {'text': '淘宝平台商户', 'y': 350},
                 {'text': '-16.80', 'y': 560}, {'text': '当前状态', 'y': 800},
                 {'text': '支付成功', 'y': 800}, {'text': '支付时间', 'y': 900},
                 {'text': '2026年10月12日11:43:23', 'y': 900}, {'text': '+4积分', 'y': 1200}]
        result = parse_payment(lines, 2000)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['amount'], '16.80')
        self.assertEqual(result[0]['category_id'], '')
        self.assertEqual(result[0]['occurred_at'], '2026-10-12 11:43:23')

    def test_amount_only_does_not_fabricate_date_or_category(self):
        result = parse_payment(['￥89.01'])[0]
        self.assertEqual(result['amount'], '89.01')
        self.assertEqual((result['occurred_at'], result['category_id']), ('', ''))
        self.assertTrue(result['review_required'])

    def test_explicit_income_and_refund_are_not_expenses(self):
        self.assertEqual(parse_payment(['收款成功', '￥25.00'])[0]['kind'], 'income')
        self.assertEqual(parse_payment(['退款成功', '￥2.00'])[0]['kind'], 'refund')


if __name__ == '__main__':
    unittest.main()
