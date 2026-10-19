"""Real local OCR -> review -> confirmation -> ledger -> original-image backup test."""
import argparse
import base64
import http.cookiejar
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from localledger.server import Application, make_server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--fixture', required=True)
    parser.add_argument('--expected-amount', default='12.30')
    parser.add_argument('--model-cache', default='data/models')
    args = parser.parse_args()
    fixture = Path(args.fixture).resolve()
    os.environ['LOCAL_LEDGER_MODEL_CACHE'] = str(Path(args.model_cache).resolve())
    with tempfile.TemporaryDirectory(prefix='ledger-real-ocr-') as directory:
        app = Application(directory, code='654321')
        assert app.recognizer.available(), 'OCR component not installed'
        server = make_server(app, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_address[1]}'
        client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        csrf = ''

        def request(path, value=None):
            headers = {'X-Local-CSRF': csrf}
            if value is not None:
                headers['Content-Type'] = 'application/json'
            body = json.dumps(value).encode() if value is not None else None
            return client.open(urllib.request.Request(base+path, data=body, headers=headers), timeout=20)

        try:
            with request('/api/login', {'code': '654321'}) as response:
                assert json.load(response)['ok']
            with request('/api/status') as response:
                csrf = json.load(response)['csrf']
            with request('/api/receipts', {'filename': fixture.name, 'content': base64.b64encode(fixture.read_bytes()).decode()}) as response:
                key = json.load(response)['id']
            deadline = time.monotonic() + 100
            while True:
                with request('/api/receipts/'+key) as response:
                    receipt = json.load(response)
                if receipt['status'] != 'queued':
                    break
                if time.monotonic() > deadline:
                    raise RuntimeError('Real OCR timed out')
                time.sleep(0.4)
            assert not receipt['error'], receipt['error']
            assert len(receipt['drafts']) == 1
            draft = receipt['drafts'][0]
            assert draft['data']['amount'] == args.expected_amount, draft['data']
            assert not app.store.transactions(), 'OCR must not post automatically'
            value = {**draft['data'], 'purpose': '集成测试食材', 'category_id': 'ingredients', 'account_id': 'cash',
                     'receipt_id': key, 'draft_id': draft['id'], 'idempotency_key': 'integration-test-request'}
            with request('/api/transactions', value) as response:
                assert json.load(response)['backup_ok']
            assert len(app.store.transactions()) == 1
            with request('/api/backup.zip') as response:
                data = response.read()
            with zipfile.ZipFile(io.BytesIO(data)) as bundle:
                originals = [name for name in bundle.namelist() if name.startswith('receipts/')]
                assert len(originals) == 1 and bundle.read(originals[0]) == fixture.read_bytes()
            print(json.dumps({'status': 'passed', 'real_local_ocr': True, 'amount': args.expected_amount,
                              'review_before_posting': True, 'transaction_count': 1,
                              'backup_contains_exact_original_image': True}, ensure_ascii=False), flush=True)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
            app.close()


if __name__ == '__main__':
    main()
