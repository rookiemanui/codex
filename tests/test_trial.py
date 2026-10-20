from pathlib import Path
import io
import tempfile
import unittest
import zipfile

from localledger.server import Application
from localledger.store import Problem, Store
from test_store import PNG


class TrialCase(unittest.TestCase):
    def test_exported_trial_restores_mode_originals_and_business_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = Application(root / 'source', test_mode=True)
            try:
                receipt = app.store.save_receipt('测试凭证.png', PNG)['id']
                app.store.set_ocr(receipt, '', [{'kind': 'expense', 'amount': '1.23'}], '')
                dish = next(x for x in app.store.menu() if x['barcode'] == '1001')
                app.store.checkout({'items': [{'menu_id': dish['id'], 'quantity': 1, 'expected_price_cents': 1800}],
                                    'expected_total_cents': 1800, 'payment_method': 'cash', 'payment_confirmed': True,
                                    'account_id': 'cash', 'occurred_at': '2026-10-20 12:00',
                                    'idempotency_key': 'restore-trial-sale'})
                expected = {name: getattr(app.store, name)() for name in ['transactions', 'products', 'menu', 'orders', 'shop', 'receipts']}
                ledger_id = app.store.ledger_id
                bundle = app.store.export_backup()
            finally:
                app.close()
            restored_root = root / 'restored'
            with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
                self.assertIn('.test-ledger.json', archive.namelist())
                archive.extractall(restored_root)
            # Forgetting --test-mode must still label a restored test ledger.
            restored = Application(restored_root)
            try:
                self.assertTrue(restored.test_mode)
                self.assertEqual(restored.store.ledger_id, ledger_id)
                for name, rows in expected.items():
                    self.assertEqual(getattr(restored.store, name)(), rows, name)
                self.assertEqual(restored.store.receipt_path(receipt).read_bytes(), PNG)
                self.assertEqual(restored.store.backup_status()['record_backup_count'], restored.store.backup_status()['event_count'])
            finally:
                restored.close()
            restored = Application(restored_root, test_mode=True)
            restored.close()

    def test_ordinary_backup_does_not_gain_trial_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Application(directory)
            try:
                with zipfile.ZipFile(io.BytesIO(app.store.export_backup())) as archive:
                    self.assertNotIn('.test-ledger.json', archive.namelist())
                self.assertFalse(app.test_mode)
            finally:
                app.close()

    def test_seeds_once_and_preserves_changes_on_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'trial'
            app = Application(root, test_mode=True)
            try:
                self.assertTrue(app.test_mode)
                self.assertEqual(len(app.store.transactions()), 3)
                self.assertEqual(app.store.summary(None)['expense_cents'], 6840)
                self.assertEqual(app.store.summary(None)['income_cents'], 12000)
                self.assertEqual(len(app.store.menu()), 3)
                app.store.add_transaction({'kind': 'expense', 'amount': '1.23', 'category_id': 'ingredients',
                                           'account_id': 'cash', 'occurred_at': '2026-10-20 12:00',
                                           'idempotency_key': 'trial-user-added'})
                ledger_id = app.store.ledger_id
            finally:
                app.close()
            app = Application(root, test_mode=True)
            try:
                self.assertEqual(app.store.ledger_id, ledger_id)
                self.assertEqual(len(app.store.transactions()), 4)
                self.assertEqual(app.store.summary(None)['expense_cents'], 6963)
            finally:
                app.close()

    def test_refuses_existing_unmarked_ledger_without_modifying_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'real'
            store = Store(root)
            before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
            with self.assertRaisesRegex(Problem, '拒绝'):
                Application(root, test_mode=True)
            after = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
            self.assertEqual(before, after)
            self.assertEqual(store.transactions(), [])

    def test_refuses_partial_or_invalid_marker_and_keeps_other_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            file = root / 'my-notes.txt'
            file.write_text('preserve me', encoding='utf-8')
            with self.assertRaises(Problem):
                Application(root, test_mode=True)
            self.assertEqual(file.read_text(encoding='utf-8'), 'preserve me')


if __name__ == '__main__':
    unittest.main()
