"""Validate 200 distinct transactions from two authenticated clients, in temporary data."""
from concurrent.futures import ThreadPoolExecutor
import http.cookiejar
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import urllib.request
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from localledger.server import Application, make_server
from localledger.store import Store


def main():
    with tempfile.TemporaryDirectory(prefix='ledger-http-volume-') as directory:
        root = Path(directory)
        app = Application(root / 'source', code='123456')
        server = make_server(app, port=0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        url = f'http://127.0.0.1:{server.server_address[1]}'
        try:
            def client_batch(indices):
                client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
                csrf = ''
                def call(path, payload=None):
                    headers = {'X-Local-CSRF': csrf}
                    data = None
                    if payload is not None:
                        headers['Content-Type'] = 'application/json'
                        data = json.dumps(payload).encode()
                    with client.open(urllib.request.Request(url + path, data=data, headers=headers), timeout=15) as response:
                        return json.load(response)
                call('/api/login', {'code': '123456'})
                csrf = call('/api/status')['csrf']
                ids = []
                for i in indices:
                    kind = 'income' if i % 2 == 0 else 'expense'
                    payload = {'kind': kind, 'amount': '1.00' if kind == 'income' else '1.23',
                               'purpose': f'200笔同日并发验证{i}', 'account_id': 'cash',
                               'category_id': 'sales' if kind == 'income' else 'ingredients',
                               'occurred_at': '2026-10-20 12:00:00', 'idempotency_key': f'http-volume-{i}'}
                    result = call('/api/transactions', payload)
                    assert result['backup_ok']
                    ids.append(result['id'])
                    if i % 10 == 0:
                        repeated = call('/api/transactions', payload)
                        assert repeated['already_saved'] and repeated['id'] == result['id']
                return ids
            with ThreadPoolExecutor(max_workers=2) as pool:
                ids = [key for batch in pool.map(client_batch, [range(0, 100), range(100, 200)]) for key in batch]
            assert len(set(ids)) == 200
            assert len(app.store.transactions()) == 200
            summary = app.store.summary('2026-10')
            assert summary['income_cents'] == 10000 and summary['expense_cents'] == 12300
            status = app.store.backup_status()
            assert status['event_count'] == status['record_backup_count'] == 200
            with zipfile.ZipFile(io.BytesIO(app.store.export_backup())) as archive:
                archive.extractall(root / 'restored')
            restored = Store(root / 'restored')
            assert restored.transactions() == app.store.transactions()
            assert restored.summary('2026-10') == summary
            print('PASS: two authenticated concurrent HTTP clients, 200 distinct same-time entries, 20 identical retries without duplicates, income100.00/expense123.00, 200 record backups and exact restore; excludes OCR throughput')
        finally:
            server.shutdown()
            server.server_close()
            app.close()
            worker.join()


if __name__ == '__main__':
    main()
