from contextlib import closing
import json
from pathlib import Path
import sqlite3
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from localledger.restore import restore_backup
from localledger.store import Problem, Store
from localledger.trial import MARKER, SIGNATURE, prepare_trial
from test_store import PNG


class RestoreCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / 'source'
        self.target = self.root / 'restored'
        self.archive = self.root / 'backup.zip'
        store = Store(self.source)
        self.receipt = store.save_receipt('合成凭证.png', PNG)['id']
        store.add_transaction({'kind': 'expense', 'amount': '12.34', 'category_id': 'ingredients',
                               'account_id': 'cash', 'occurred_at': '2026-10-22 12:00',
                               'receipt_id': self.receipt, 'idempotency_key': 'synthetic-restore'})
        self.ledger_id = store.ledger_id
        self.image_name = store.receipt_path(self.receipt).name
        self.archive.write_bytes(store.export_backup())

    def tearDown(self):
        self.temporary.cleanup()

    def rewrite(self, replacements=None, removed=(), extra=()):
        replacements = replacements or {}
        with zipfile.ZipFile(self.archive) as source:
            contents = [(entry, source.read(entry)) for entry in source.infolist()]
        with zipfile.ZipFile(self.archive, 'w', zipfile.ZIP_DEFLATED) as output:
            for entry, content in contents:
                if entry.filename not in removed:
                    output.writestr(entry, replacements.get(entry.filename, content))
            for name, content in extra:
                output.writestr(name, content)

    def change_database(self, statement):
        with zipfile.ZipFile(self.archive) as source:
            raw = source.read('ledger.sqlite3')
        database = self.root / 'alter.sqlite3'
        database.write_bytes(raw)
        with closing(sqlite3.connect(database)) as db:
            db.executescript(statement)
            db.commit()
        self.rewrite({'ledger.sqlite3': database.read_bytes()})

    def assert_rejected_cleanly(self):
        with self.assertRaises(Problem):
            restore_backup(self.archive, self.target)
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.root.glob('.ledger-restore-*')), [])

    def test_restores_business_data_originals_audit_and_provenance(self):
        provenance = {'synthetic': True, 'source': 'fixture'}
        self.rewrite(extra=[('legacy-source.json', json.dumps(provenance))])
        result = restore_backup(self.archive, self.target)
        self.assertEqual(result['destination'], str(self.target))
        self.assertEqual(result['ledger_id'], self.ledger_id)
        self.assertFalse(result['test_mode'])
        self.assertEqual(result['counts']['transactions'], 1)
        self.assertEqual(result['counts']['receipts'], 1)
        self.assertEqual((self.target / 'receipts' / self.image_name).read_bytes(), PNG)
        self.assertEqual((self.target / 'backups/receipts' / self.image_name).read_bytes(), PNG)
        self.assertEqual(json.loads((self.target / 'legacy-source.json').read_text()), provenance)
        self.assertEqual(len(list((self.target / 'backups/records').glob('*.json'))), result['counts']['events'])
        self.assertFalse((self.target / 'access.json').exists())
        with closing(sqlite3.connect(self.target / 'ledger.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT amount_cents FROM transactions').fetchone()[0], 1234)
        # Renaming and deleting proves SQLite handles are closed on Windows.
        renamed = self.root / 'renamed'
        self.target.rename(renamed)
        (renamed / 'ledger.sqlite3').unlink()

    def test_accepts_empty_destination(self):
        self.target.mkdir()
        restore_backup(self.archive, self.target)
        self.assertTrue((self.target / 'ledger.sqlite3').is_file())

    def test_preserves_trial_marker_and_sales(self):
        trial = self.root / 'trial'
        prepare_trial(trial)
        store = Store(trial)
        dish = store.menu()[0]
        store.checkout({'items': [{'menu_id': dish['id'], 'quantity': 1,
                                   'expected_price_cents': dish['price_cents']}],
                        'expected_total_cents': dish['price_cents'], 'payment_method': 'cash',
                        'payment_confirmed': True, 'account_id': 'cash',
                        'occurred_at': '2026-10-22 12:00', 'idempotency_key': 'synthetic-sale'})
        self.archive.write_bytes(store.export_backup())
        result = restore_backup(self.archive, self.target)
        self.assertTrue(result['test_mode'])
        self.assertEqual(result['counts']['orders'], 1)
        self.assertEqual(json.loads((self.target / MARKER).read_text()), SIGNATURE)
        self.assertEqual(Store(self.target).orders(), store.orders())

    def test_refuses_existing_files_without_modification(self):
        self.target.mkdir()
        (self.target / 'ledger.sqlite3').write_bytes(b'keep this real-looking fixture')
        with self.assertRaisesRegex(Problem, '拒绝覆盖'):
            restore_backup(self.archive, self.target)
        self.assertEqual((self.target / 'ledger.sqlite3').read_bytes(), b'keep this real-looking fixture')

    def test_refuses_non_directory_destination(self):
        self.target.write_bytes(b'keep')
        with self.assertRaises(Problem):
            restore_backup(self.archive, self.target)
        self.assertEqual(self.target.read_bytes(), b'keep')

    def test_refuses_relative_destination(self):
        with self.assertRaisesRegex(Problem, '绝对路径'):
            restore_backup(self.archive, 'relative-restore-fixture')

    def test_path_traversal_absolute_and_windows_names(self):
        original = self.archive.read_bytes()
        for name in ('../escape', '/absolute', 'C:/escape', 'receipts/../escape',
                     'receipts\\escape', 'receipts//escape', 'ledger.sqlite3:secret',
                     'ledger.sqlite3.', 'ledger.sqlite3 ', 'access.json'):
            with self.subTest(name=name):
                self.archive.write_bytes(original)
                self.rewrite(extra=[(name, b'unsafe')])
                self.assert_rejected_cleanly()
        self.assertFalse((self.root / 'escape').exists())

    def test_rejects_symbolic_link_member(self):
        entry = zipfile.ZipInfo('legacy-source.json')
        entry.create_system = 3
        entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        self.rewrite(extra=[(entry, '../elsewhere')])
        self.assert_rejected_cleanly()

    def test_rejects_case_colliding_names(self):
        self.rewrite(extra=[('LEDGER.SQLITE3', b'other')])
        self.assert_rejected_cleanly()

    def test_rejects_missing_or_invalid_database(self):
        original = self.archive.read_bytes()
        self.rewrite(removed=['ledger.sqlite3'])
        self.assert_rejected_cleanly()
        self.archive.write_bytes(original)
        self.rewrite({'ledger.sqlite3': b'not sqlite'})
        self.assert_rejected_cleanly()

    def test_rejects_wrong_schema_missing_columns_and_executable_schema(self):
        original = self.archive.read_bytes()
        for statement in ("UPDATE metadata SET value='999' WHERE key='schema_version';",
                          'ALTER TABLE transactions RENAME COLUMN amount_cents TO missing_amount;',
                          'CREATE VIEW unexpected AS SELECT * FROM transactions;',
                          "CREATE TRIGGER bad AFTER INSERT ON accounts BEGIN DELETE FROM transactions; END;"):
            with self.subTest(statement=statement):
                self.archive.write_bytes(original)
                self.change_database(statement)
                self.assert_rejected_cleanly()

    def test_rejects_broken_foreign_key(self):
        self.change_database("UPDATE transactions SET account_id='missing-fixture';")
        self.assert_rejected_cleanly()

    def test_rejects_missing_and_corrupt_original(self):
        original = self.archive.read_bytes()
        name = 'receipts/' + self.image_name
        self.rewrite(removed=[name])
        self.assert_rejected_cleanly()
        self.archive.write_bytes(original)
        self.rewrite({name: b'corrupted synthetic image'})
        self.assert_rejected_cleanly()

    def test_rejects_missing_or_altered_audit_record(self):
        original = self.archive.read_bytes()
        with zipfile.ZipFile(self.archive) as source:
            name = next(n for n in source.namelist() if n.startswith('backups/records/'))
        self.rewrite(removed=[name])
        self.assert_rejected_cleanly()
        self.archive.write_bytes(original)
        self.rewrite({name: '{}'})
        self.assert_rejected_cleanly()

    def test_rejects_invalid_trial_marker(self):
        self.rewrite(extra=[(MARKER, '{}')])
        self.assert_rejected_cleanly()

    def test_rejects_size_count_and_ratio_limits(self):
        for constant, limit in (('MAX_MEMBERS', 1), ('MAX_FILE_BYTES', 100),
                                ('MAX_TOTAL_BYTES', 100), ('MAX_COMPRESSION_RATIO', 1)):
            with self.subTest(constant=constant), patch('localledger.restore.' + constant, limit):
                self.assert_rejected_cleanly()

    def test_bad_archive_leaves_existing_empty_destination_empty(self):
        self.target.mkdir()
        self.archive.write_bytes(b'not zip')
        with self.assertRaises(Problem):
            restore_backup(self.archive, self.target)
        self.assertEqual(list(self.target.iterdir()), [])

    def test_publication_failure_removes_staging(self):
        with patch('localledger.restore.os.rename', side_effect=OSError('synthetic rename failure')):
            self.assert_rejected_cleanly()

    def test_another_writer_is_not_overwritten(self):
        from localledger import restore
        validate = restore._validate

        def add_concurrent_file(staging):
            result = validate(staging)
            self.target.mkdir()
            (self.target / 'concurrent.txt').write_text('keep', encoding='utf-8')
            return result

        with patch('localledger.restore._validate', side_effect=add_concurrent_file):
            with self.assertRaises(Problem):
                restore_backup(self.archive, self.target)
        self.assertEqual((self.target / 'concurrent.txt').read_text(), 'keep')
        self.assertFalse((self.target / 'ledger.sqlite3').exists())
        self.assertEqual(list(self.root.glob('.ledger-restore-*')), [])


if __name__ == '__main__':
    unittest.main()
