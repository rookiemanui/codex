from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import secrets
import shutil
import threading
import time
import traceback
from urllib.parse import parse_qs, urlsplit

from . import __version__
from .access import load_password, verify_password, public_origin
from .recognition import Recognizer, parse_payment
from .store import Problem, Store
from .trial import is_trial, prepare_trial


STATIC = Path(__file__).resolve().parent / 'web'


class LedgerHTTPServer(ThreadingHTTPServer):
    # server_close must wait for active writes and their backups before the
    # controller releases the data-directory lock or exits the process.
    daemon_threads = False

    def get_request(self):
        client, address = super().get_request()
        client.settimeout(15)
        return client, address


class Application:
    def __init__(self, directory, code=None, public_url=None, test_mode=False):
        self.test_mode = test_mode or is_trial(directory)
        if self.test_mode:
            directory = prepare_trial(directory)
        self.public_url = public_origin(public_url)
        self.password = load_password(directory)
        if self.public_url and not self.password:
            raise Problem("外网模式必须先在电脑上运行 --set-password 设置独立密码")
        self.store = Store(directory)
        # The code argument remains accepted for older callers; local access is direct.
        self.recognizer = Recognizer(directory)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='local-ocr')
        self.sessions = {}
        self.attempts = {}
        self.auth_lock = threading.Lock()
        for receipt in self.store.receipts():
            if receipt['status'] == 'queued':
                self.executor.submit(self.recognize, receipt['id'])

    def session(self, cookie):
        try:
            cookies = SimpleCookie(cookie or '')
            key = cookies['ledger_session'].value if 'ledger_session' in cookies else ''
        except Exception:
            return None
        with self.auth_lock:
            value = self.sessions.get(key)
            if value and value['expires'] > time.time():
                return value
        return None

    def login(self, code, ip):
        with self.auth_lock:
            if self.password:
                attempts = [t for t in self.attempts.get(ip, []) if time.time()-t < 60]
                if len(attempts) >= 5:
                    raise Problem('尝试次数较多，请一分钟后重试')
                if not verify_password(self.password, code):
                    self.attempts[ip] = attempts + [time.time()]
                    raise Problem('密码不正确')
                self.attempts.pop(ip, None)
            self.sessions = {k: v for k, v in self.sessions.items() if v['expires'] > time.time()}
            key = secrets.token_urlsafe(32)
            self.sessions[key] = {'csrf': secrets.token_urlsafe(24), 'expires': time.time()+12*60*60}
            return key

    def recognize(self, key):
        try:
            raw, drafts, error = self.recognizer.recognize(self.store.receipt_path(key), self.store.rules())
        except Exception as exc:
            raw, drafts = '', parse_payment([])
            error = '自动识别暂未完成，可对照原图手工填写。原因：' + str(exc)[:400]
        try:
            self.store.set_ocr(key, raw, drafts, error)
        except Exception:
            traceback.print_exc()

    def close(self):
        # Preserve queued receipt rows; the next startup resumes them.
        self.executor.shutdown(wait=True, cancel_futures=True)


def make_server(application, host='127.0.0.1', port=8765):
    if application.public_url and host != '127.0.0.1':
        raise Problem('外网模式仅监听 127.0.0.1，请由 HTTPS 反向代理提供外网入口')

    class Handler(BaseHTTPRequestHandler):
        server_version = 'LocalLedger'

        def log_message(self, fmt, *args):
            # Request paths contain no connection codes or account values.
            pass

        def send(self, body, code=200, mime='application/json; charset=utf-8', headers=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, ensure_ascii=False).encode('utf-8')
            if isinstance(body, str):
                body = body.encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def authenticated(self, mutation=False):
            session = application.session(self.headers.get('Cookie'))
            if not session:
                self.send({'error': '请先登录账本'}, 401)
                return False
            if mutation:
                origin = self.headers.get('Origin')
                if origin and urlsplit(origin).netloc != self.headers.get('Host'):
                    self.send({'error': '请求来源不匹配'}, 403)
                    return False
                if not secrets.compare_digest(self.headers.get('X-Local-CSRF', ''), session['csrf']):
                    self.send({'error': '连接已变化，请刷新页面后重试'}, 403)
                    return False
            return True

        def remote_request_valid(self):
            if not application.public_url:
                return True
            expected = urlsplit(application.public_url).netloc
            if self.headers.get('Host', '').lower() != expected or self.headers.get('X-Forwarded-Proto') != 'https':
                self.send({'error': '外网模式请使用配置的 HTTPS 地址访问'}, 403)
                return False
            return True

        def do_GET(self):
            if not self.remote_request_valid():
                return
            try:
                path = urlsplit(self.path).path
                args = parse_qs(urlsplit(self.path).query)
                if path == '/api/status':
                    session = application.session(self.headers.get('Cookie'))
                    headers = None
                    if not session and not application.password:
                        key = application.login('', self.client_address[0])
                        session = application.session('ledger_session=' + key)
                        headers = {'Set-Cookie': f'ledger_session={key}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200'}
                    self.send({'authenticated': bool(session), 'csrf': session['csrf'] if session else '',
                               'ledger_id': application.store.ledger_id if session else None, 'version': __version__,
                               'auth_mode': 'password' if application.password else 'direct',
                               'test_mode': application.test_mode}, headers=headers)
                    return
                if not path.startswith('/api/'):
                    files = {'/': 'index.html', '/app.js': 'app.js', '/pos.js': 'pos.js', '/checks.js': 'checks.js', '/style.css': 'style.css', '/icon.svg': 'icon.svg'}
                    if path not in files:
                        self.send({'error': '页面不存在'}, 404)
                        return
                    file = STATIC / files[path]
                    self.send(file.read_bytes(), mime=mimetypes.guess_type(file.name)[0] or 'application/octet-stream')
                    return
                if not self.authenticated():
                    return
                store = application.store
                month = args.get('month', [time.strftime('%Y-%m')])[0]
                if path == '/api/options':
                    self.send(store.options())
                elif path == '/api/summary':
                    self.send(store.summary(month))
                elif path == '/api/transactions':
                    try:
                        limit = int(args['limit'][0]) if 'limit' in args else None
                        offset = int(args.get('offset', ['0'])[0])
                    except ValueError as exc:
                        raise Problem('分页参数无效') from exc
                    self.send(store.transactions(args.get('month', [None])[0], args.get('search', [''])[0], limit, offset))
                elif path == '/api/receipts':
                    self.send(store.receipts())
                elif path.startswith('/api/receipts/'):
                    parts = path.strip('/').split('/')
                    if len(parts) == 4 and parts[3] == 'file':
                        file = store.receipt_path(parts[2])
                        self.send(file.read_bytes(), mime=mimetypes.guess_type(file.name)[0] or 'image/png')
                    elif len(parts) == 3:
                        self.send(store.receipt(parts[2]))
                    else:
                        self.send({'error': '路径不存在'}, 404)
                elif path == '/api/daily-check':
                    self.send(store.daily_check(args.get('day', [time.strftime('%Y-%m-%d')])[0], args.get('scope', ['all'])[0]))
                elif path == '/api/menu':
                    self.send(store.menu())
                elif path == '/api/shop':
                    self.send(store.shop())
                elif path == '/api/orders':
                    self.send(store.orders(args.get('month', [None])[0]))
                elif path.startswith('/api/orders/'):
                    self.send(store.order(path.rsplit('/', 1)[1]))
                elif path == '/api/products':
                    self.send(store.products())
                elif path == '/api/movements':
                    self.send(store.movements())
                elif path == '/api/rules':
                    self.send(store.rules())
                elif path == '/api/backup-status':
                    self.send({**store.backup_status(), 'ocr_available': application.recognizer.available(),
                               'version': __version__, 'access_mode': ('HTTPS 外网入口：' + application.public_url if application.public_url else ('本机或局域网，使用独立密码' if application.password else '本机或同一局域网，打开地址直接进入'))})
                elif path == '/api/export.csv':
                    self.send(store.export_csv(), mime='text/csv; charset=utf-8', headers={'Content-Disposition': 'attachment; filename="ledger.csv"'})
                elif path == '/api/inventory.csv':
                    name = 'inventory-' + time.strftime('%Y%m%d-%H%M') + '.csv'
                    self.send(store.export_inventory_csv(), mime='text/csv; charset=utf-8',
                              headers={'Content-Disposition': f'attachment; filename="{name}"'})
                elif path == '/api/backup.zip':
                    archive = store.backup_file()
                    try:
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/zip')
                        self.send_header('Content-Length', str(archive.stat().st_size))
                        self.send_header('Content-Disposition', 'attachment; filename="ledger-backup.zip"')
                        self.send_header('X-Content-Type-Options', 'nosniff')
                        self.send_header('Cache-Control', 'no-store')
                        self.end_headers()
                        with archive.open('rb') as file:
                            shutil.copyfileobj(file, self.wfile, length=1024*1024)
                    finally:
                        archive.unlink(missing_ok=True)
                else:
                    self.send({'error': '接口不存在'}, 404)
            except Problem as exc:
                self.send({'error': str(exc)}, 400)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
                traceback.print_exc()
                self.send({'error': '本地操作失败，请查看电脑启动窗口；已保存的账目不会被自动清除'}, 500)

        def do_POST(self):
            if not self.remote_request_valid():
                return
            try:
                path = urlsplit(self.path).path
                size = int(self.headers.get('Content-Length', '0'))
                if size < 1 or size > 29 * 1024 * 1024:
                    raise Problem('请求为空或文件过大')
                try:
                    value = json.loads(self.rfile.read(size))
                except (ValueError, UnicodeDecodeError) as exc:
                    raise Problem('请求格式错误') from exc
                if not isinstance(value, dict):
                    raise Problem('请求格式错误')
                if path == '/api/login':
                    origin = self.headers.get('Origin')
                    if origin and urlsplit(origin).netloc != self.headers.get('Host'):
                        self.send({'error': '请求来源不匹配'}, 403)
                        return
                    key = application.login(value.get('code', ''), self.client_address[0])
                    self.send({'ok': True}, headers={'Set-Cookie': f'ledger_session={key}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200' + ('; Secure' if application.public_url else '')})
                    return
                if not self.authenticated(mutation=True):
                    return
                store = application.store
                if path == '/api/logout':
                    cookie = SimpleCookie(self.headers.get('Cookie') or '')
                    if 'ledger_session' in cookie:
                        with application.auth_lock:
                            application.sessions.pop(cookie['ledger_session'].value, None)
                    self.send({'ok': True}, headers={'Set-Cookie': 'ledger_session=; Max-Age=0; Path=/; HttpOnly; SameSite=Strict' + ('; Secure' if application.public_url else '')})
                elif path == '/api/menu':
                    self.send(store.save_menu(value))
                elif path.startswith('/api/menu/'):
                    self.send(store.save_menu(value, path.rsplit('/', 1)[1]))
                elif path == '/api/daily-check':
                    self.send(store.save_daily_check(value))
                elif path == '/api/shop':
                    self.send(store.save_shop(value))
                elif path == '/api/checkout':
                    self.send(store.checkout(value))
                elif path.startswith('/api/orders/') and path.endswith('/refund'):
                    self.send(store.refund_order(path.strip('/').split('/')[2], value))
                elif path == '/api/transactions':
                    self.send(store.add_transaction(value))
                elif path.startswith('/api/transactions/'):
                    parts = path.strip('/').split('/')
                    if len(parts) == 4 and parts[3] == 'category':
                        self.send(store.reclassify(parts[2], value))
                    elif len(parts) == 4 and parts[3] == 'void':
                        self.send(store.void(parts[2]))
                    else:
                        self.send({'error': '接口不存在'}, 404)
                elif path == '/api/accounts':
                    self.send(store.add_account(value))
                elif path == '/api/categories':
                    self.send(store.add_category(value))
                elif path == '/api/receipts':
                    try:
                        content = base64.b64decode(value.get('content', ''), validate=True)
                    except ValueError as exc:
                        raise Problem('图片上传不完整，请重新选择文件') from exc
                    result = store.save_receipt(value.get('filename', 'receipt.png'), content)
                    if not result['duplicate_file']:
                        application.executor.submit(application.recognize, result['id'])
                    self.send(result)
                elif path == '/api/products':
                    self.send(store.add_product(value))
                elif path == '/api/movements':
                    self.send(store.add_movement(value))
                elif path.startswith('/api/movements/') and path.endswith('/reverse'):
                    parts = path.strip('/').split('/')
                    if len(parts) != 4:
                        self.send({'error': '接口不存在'}, 404)
                    else:
                        self.send(store.reverse_movement(parts[2], value))
                elif path.startswith('/api/rules/'):
                    self.send(store.delete_rule(path.rsplit('/', 1)[1]))
                elif path == '/api/backup':
                    with store.lock:
                        store.recover_backups()
                        store._backup()
                    self.send({'ok': True, 'backup_ok': True})
                else:
                    self.send({'error': '接口不存在'}, 404)
            except Problem as exc:
                self.send({'error': str(exc)}, 400)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
                traceback.print_exc()
                self.send({'error': '本地操作失败，请查看电脑启动窗口；重复提交不会重复记账'}, 500)

    return LedgerHTTPServer((host, port), Handler)
