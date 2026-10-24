import base64
import http.cookiejar
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

from localledger.server import Application, make_server
from test_store import PNG


class HTTPCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = Application(self.directory.name, code='123456')
        self.server = make_server(self.app, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}'
        self.client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.csrf = ''

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.app.close()
        self.directory.cleanup()

    def call(self, path, data=None, csrf=True, headers=None):
        request = urllib.request.Request(self.url+path, headers=headers or {})
        if data is not None:
            request.data = json.dumps(data).encode()
            request.add_header('Content-Type', 'application/json')
            if csrf:
                request.add_header('X-Local-CSRF', self.csrf)
        return self.client.open(request, timeout=10)

    def login(self):
        with self.call('/api/status') as response:
            self.assertIn('HttpOnly', response.headers['Set-Cookie'])
            self.assertIn('SameSite=Strict', response.headers['Set-Cookie'])
            status = json.load(response)
            self.assertTrue(status['authenticated'])
            self.assertEqual(status['auth_mode'], 'direct')
            self.csrf = status['csrf']

    def test_opening_status_enters_local_ledger_directly(self):
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.call('/api/transactions')
        self.assertEqual(raised.exception.code, 401)
        with self.call('/') as response:
            self.assertIn('档口账本', response.read().decode())
        self.login()
        with self.call('/api/transactions') as response:
            self.assertEqual(json.load(response), [])

    def test_mutations_require_csrf_and_matching_origin(self):
        self.login()
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.call('/api/accounts', {'name': '未授权操作'}, csrf=False)
        self.assertEqual(raised.exception.code, 403)
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.call('/api/accounts', {'name': '跨站操作'}, headers={'Origin': 'https://example.invalid'})
        self.assertEqual(raised.exception.code, 403)

    def test_real_api_saves_then_exports_transaction(self):
        self.login()
        value = {'kind': 'expense', 'amount': '16.80', 'occurred_at': '2026-10-12 11:43:23',
                 'account_id': 'wechat', 'category_id': 'ingredients', 'purpose': '猪肚鸡调料',
                 'idempotency_key': 'http-test-request'}
        for _ in range(2):
            with self.call('/api/transactions', value) as response:
                self.assertTrue(json.load(response)['backup_ok'])
        with self.call('/api/summary?month=2026-10') as response:
            result = json.load(response)
            self.assertEqual((result['expense_cents'], result['transaction_count']), (1680, 1))
        with self.call('/api/export.csv') as response:
            self.assertIn('猪肚鸡调料', response.read().decode('utf-8-sig'))

    def test_upload_preserves_real_image_bytes_and_creates_review(self):
        self.login()
        with self.call('/api/receipts', {'filename': '测试凭证.png', 'content': base64.b64encode(PNG).decode()}) as response:
            key = json.load(response)['id']
        with self.call(f'/api/receipts/{key}/file') as response:
            self.assertEqual(response.read(), PNG)
        deadline = time.monotonic() + 5
        while True:
            with self.call(f'/api/receipts/{key}') as response:
                result = json.load(response)
            if result['status'] != 'queued':
                break
            if time.monotonic() > deadline:
                self.fail('Local OCR fallback did not produce a review draft')
            time.sleep(0.05)
        self.assertTrue(result['drafts'])
        self.assertEqual(self.app.store.transactions(), [])

    def test_receipt_queue_resumes_after_restart(self):
        key = self.app.store.save_receipt('中断前上传.png', PNG)['id']
        self.app.close()
        self.app = Application(self.directory.name, code='123456')
        deadline = time.monotonic() + 5
        while self.app.store.receipt(key)['status'] == 'queued':
            if time.monotonic() > deadline:
                self.fail('Unfinished screenshot job did not resume after restart')
            time.sleep(0.05)
        self.assertTrue(self.app.store.receipt(key)['drafts'])

class RemoteHTTPCase(unittest.TestCase):
    def setUp(self):
        from localledger.access import set_password
        self.directory = tempfile.TemporaryDirectory()
        set_password(self.directory.name, 'owner-password-123456')
        self.app = Application(self.directory.name, public_url='https://shop.example.com')
        self.server = make_server(self.app, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.app.close()
        self.directory.cleanup()

    def request(self, path, data=None, extra=None):
        headers = {'Host': 'shop.example.com', 'X-Forwarded-Proto': 'https', **(extra or {})}
        if data is not None:
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(self.url + path, data=json.dumps(data).encode() if data is not None else None, headers=headers)
        return urllib.request.urlopen(req, timeout=10)

    def test_tls_proxy_host_secure_cookie_csrf_and_logout(self):
        for extra in [{'Host': 'evil.example.com'}, {'X-Forwarded-Proto': 'http'}]:
            with self.assertRaises(urllib.error.HTTPError) as raised:
                self.request('/api/status', extra=extra)
            self.assertEqual(raised.exception.code, 403)
        with self.request('/api/status') as response:
            self.assertEqual(json.load(response)['auth_mode'], 'password')
        with self.request('/api/login', {'code': 'owner-password-123456'}, {'Origin': 'https://shop.example.com'}) as response:
            self.assertIn('Secure', response.headers['Set-Cookie'])
            cookie = response.headers['Set-Cookie'].split(';')[0]
        with self.request('/api/status', extra={'Cookie': cookie}) as response:
            csrf = json.load(response)['csrf']
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request('/api/shop', {'name': 'Invalid'}, {'Cookie': cookie, 'Origin': 'https://shop.example.com'})
        self.assertEqual(raised.exception.code, 403)
        headers = {'Cookie': cookie, 'X-Local-CSRF': csrf, 'Origin': 'https://shop.example.com'}
        with self.request('/api/shop', {'name': '测试餐饮店'}, headers) as response:
            self.assertTrue(json.load(response)['backup_ok'])
        with self.request('/api/logout', {}, headers) as response:
            self.assertIn('Max-Age=0', response.headers['Set-Cookie'])
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request('/api/shop', extra={'Cookie': cookie})
        self.assertEqual(raised.exception.code, 401)

    def test_password_login_is_rate_limited(self):
        for _ in range(5):
            with self.assertRaises(urllib.error.HTTPError):
                self.request('/api/login', {'code': 'wrong-password'})
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request('/api/login', {'code': 'owner-password-123456'})
        self.assertIn('一分钟', raised.exception.read().decode())


if __name__ == '__main__':
    unittest.main()
