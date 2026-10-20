from pathlib import Path
import tempfile
import unittest

from localledger.server import Application
from localledger.store import Problem, Store


class TrialCase(unittest.TestCase):
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
