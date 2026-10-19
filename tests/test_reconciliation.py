import tempfile
import unittest
from uuid import uuid4
from localledger.store import Store, Problem


class ReconciliationCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def entry(self, kind, amount, **extra):
        return self.store.add_transaction({'kind': kind, 'amount': amount, 'occurred_at': '2026-10-19 12:00',
            'account_id': 'cash', 'category_id': 'sales' if kind == 'income' else 'ingredients', 'idempotency_key': uuid4().hex, **extra})

    def check(self, amount, **extra):
        return self.store.save_daily_check({'day': '2026-10-19', 'scope': 'all', 'kind': 'expense', 'amount': amount, 'version': 0, **extra})

    def test_total_reconciliation_does_not_add_income_or_expense(self):
        self.entry('expense', '16.80')
        self.entry('expense', '9.26')
        before = self.store.transactions()
        self.check('26.06')
        row = self.store.daily_check('2026-10-19')['checks'][1]
        self.assertTrue(row['matched'])
        self.assertEqual(row['difference_cents'], 0)
        self.assertEqual(self.store.transactions(), before)
        self.assertEqual(self.store.backup_status()['event_count'], self.store.backup_status()['record_backup_count'])

    def test_changed_ledger_reopens_difference_without_changing_reference_total(self):
        self.entry('expense', '16.80')
        self.check('16.80')
        self.entry('expense', '2')
        row = self.store.daily_check('2026-10-19')['checks'][1]
        self.assertFalse(row['matched'])
        self.assertEqual((row['actual_cents'], row['expected_cents'], row['difference_cents']), (1880, 1680, 200))

    def test_refunds_transfers_voids_and_account_scopes(self):
        expense = self.entry('expense', '10')['id']
        self.entry('refund', '3', refund_of=expense)
        self.entry('transfer', '100', to_account_id='wechat')
        self.entry('expense', '4', account_id='wechat')
        cancelled = self.entry('expense', '9')['id']
        self.store.void(cancelled)
        all_check = self.store.daily_check('2026-10-19')
        self.assertEqual(all_check['checks'][1]['actual_cents'], 1100)
        self.assertEqual(all_check['transaction_count'], 3)
        cash = self.store.daily_check('2026-10-19', 'cash')
        self.assertEqual(cash['checks'][1]['actual_cents'], 700)

    def test_negative_net_day_and_zero_are_valid(self):
        expense = self.entry('expense', '10', occurred_at='2026-10-18 12:00')['id']
        self.entry('refund', '3', refund_of=expense)
        self.check('-3')
        self.assertTrue(self.store.daily_check('2026-10-19')['checks'][1]['matched'])
        self.check('0', kind='income')
        self.assertTrue(self.store.daily_check('2026-10-19')['checks'][0]['matched'])

    def test_stale_device_cannot_overwrite_another_device(self):
        self.check('10')
        with self.assertRaises(Problem):
            self.check('20')
        self.check('15', version=1)
        row = self.store.daily_check('2026-10-19')['checks'][1]
        self.assertEqual((row['expected_cents'], row['version']), (1500, 2))

    def test_invalid_date_and_account_are_rejected(self):
        for values in [{'day': '2026-02-30'}, {'scope': 'missing-account'}, {'version': -1}, {'amount': '1.005'}]:
            with self.subTest(values=values), self.assertRaises(Problem):
                self.check('10', **values) if 'amount' not in values else self.check(values['amount'])
        with self.assertRaises(Problem):
            self.store.transactions('2026-13')

    def test_query_refunded_totals_preserves_partial_voided_and_cross_month_refunds(self):
        key = self.entry('expense', '10')['id']
        self.entry('refund', '2', refund_of=key, occurred_at='2026-11-01 10:00')
        invalid = self.entry('refund', '3', refund_of=key)['id']
        self.store.void(invalid)
        row = next(x for x in self.store.transactions('2026-10') if x['id'] == key)
        self.assertEqual(row['refunded_cents'], 200)
        self.assertEqual(self.store.summary('2026-10')['expense_cents'], 1000)
        self.assertEqual(self.store.summary('2026-11')['expense_cents'], -200)

    def test_ledger_id_is_stable_and_different_between_independent_ledgers(self):
        again = Store(self.temp.name)
        self.assertEqual(self.store.ledger_id, again.ledger_id)
        with tempfile.TemporaryDirectory() as other:
            self.assertNotEqual(self.store.ledger_id, Store(other).ledger_id)

    def test_limited_recent_rows_do_not_limit_monthly_totals(self):
        for day in [18, 19, 20]:
            self.entry('expense', '2', occurred_at=f'2026-10-{day} 12:00')
        self.assertEqual(len(self.store.transactions('2026-10', limit=1)), 1)
        self.assertTrue(self.store.transactions('2026-10', limit=1, offset=1)[0]['occurred_at'].startswith('2026-10-19'))
        self.assertEqual(self.store.summary('2026-10')['expense_cents'], 600)
        for arguments in [{'limit': 0}, {'limit': 5001}, {'limit': True}, {'offset': -1}, {'offset': 1}]:
            with self.subTest(arguments=arguments), self.assertRaises(Problem):
                self.store.transactions(**arguments)
