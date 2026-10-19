"""Compare the former N+1 query pattern with the indexed aggregate; synthetic ledger only."""
import json
from pathlib import Path
import sys
import tempfile
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from localledger.store import Store


def main():
    with tempfile.TemporaryDirectory(prefix='ledger-benchmark-') as directory:
        store = Store(directory)
        with store.connect() as db:
            rows = [(f'exp-{i}', 'expense', 100, f'2026-10-{i % 30 + 1:02d} 12:00:00', 'ingredients', 'cash', f'fixture-{i}', None, f'exp-{i}') for i in range(6000)]
            rows += [(f'ref-{i}', 'refund', 30, '2026-10-31 12:00:00', 'ingredients', 'cash', f'refund-fixture-{i}', f'exp-{i}', f'ref-{i}') for i in range(300)]
            db.executemany('''INSERT INTO transactions(id,kind,amount_cents,occurred_at,category_id,account_id,
                purpose,refund_of,idempotency_key,created_at) VALUES(?,?,?,?,?,?,?,?,?, '2026-10-19 12:00:00')''', rows)
            db.execute('DROP INDEX idx_tx_refund')
        start = perf_counter()
        with store.connect() as db:
            old = db.execute("SELECT id FROM transactions WHERE substr(occurred_at,1,7)='2026-10'").fetchall()
            old_refunds = {r['id']: db.execute('SELECT COALESCE(SUM(amount_cents),0) FROM transactions WHERE refund_of=? AND voided_at IS NULL', (r['id'],)).fetchone()[0] for r in old}
        old_seconds = perf_counter()-start
        with store.connect() as db:
            db.execute('CREATE INDEX idx_tx_refund ON transactions(refund_of) WHERE voided_at IS NULL')
        start = perf_counter()
        new = store.transactions('2026-10')
        new_seconds = perf_counter()-start
        assert {r['id']: r['refunded_cents'] for r in new} == old_refunds
        assert store.summary('2026-10')['expense_cents'] == 591000
        print(json.dumps({'synthetic': True, 'rows': len(new), 'former_select_count': len(new)+1,
            'new_select_count': 1, 'former_seconds': round(old_seconds, 4), 'new_seconds': round(new_seconds, 4),
            'net_expense_cents': 591000, 'note': 'Current cloud machine only, excludes OCR and backups; not a device performance promise.'}))


if __name__ == '__main__':
    main()
