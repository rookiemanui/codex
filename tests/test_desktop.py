import errno
import http.cookiejar
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.request

from localledger.desktop import (DataDirectoryLock, LedgerService, _cmd_argument, create_restore_launcher,
                                 open_app_window, parse_arguments, restore_for_desktop)
from localledger.store import Problem, Store


class DesktopLifecycleCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name) / '中文店铺 账本'
        self.services = []

    def tearDown(self):
        for service in self.services:
            service.stop()
        self.temporary.cleanup()

    def service(self, directory=None, **kwargs):
        service = LedgerService(directory or self.directory, host='127.0.0.1', port=kwargs.pop('port', 0), **kwargs)
        self.services.append(service)
        return service

    @staticmethod
    def client(service):
        client = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        with client.open(service.url + '/api/status', timeout=5) as response:
            status = json.load(response)
            assert status['authenticated'] and status['auth_mode'] == 'direct'
            csrf = status['csrf']
        return client, csrf

    def test_two_clients_share_data_and_restart_preserves_it(self):
        service = self.service().start()
        first, csrf = self.client(service)
        second, _ = self.client(service)
        payload = {'kind': 'income', 'amount': '38.50', 'occurred_at': '2026-10-22 12:30:00',
                   'account_id': 'cash', 'category_id': 'sales', 'purpose': '测试午市',
                   'idempotency_key': 'desktop-life-cycle'}
        request = urllib.request.Request(service.url + '/api/transactions', data=json.dumps(payload).encode(),
                                         headers={'Content-Type': 'application/json', 'X-Local-CSRF': csrf})
        with first.open(request, timeout=5) as response:
            self.assertTrue(json.load(response)['backup_ok'])
        with second.open(service.url + '/api/summary?month=2026-10', timeout=5) as response:
            self.assertEqual(json.load(response)['income_cents'], 3850)
        service.stop()
        self.assertFalse(service.running)
        restarted = self.service().start()
        client, _ = self.client(restarted)
        with client.open(restarted.url + '/api/summary?month=2026-10', timeout=5) as response:
            self.assertEqual(json.load(response)['income_cents'], 3850)

    def test_same_directory_cannot_start_twice_even_on_different_ports(self):
        first = self.service().start()
        with self.assertRaisesRegex(Problem, '已经打开'):
            self.service().start()
        self.assertTrue(first.running)

    def test_safe_stop_waits_for_inflight_write_and_backup_before_unlocking(self):
        service = self.service().start()
        application = service.application
        client, csrf = self.client(service)
        entered, release, stopped = threading.Event(), threading.Event(), threading.Event()
        results, errors = [], []
        original = application.store.add_transaction

        def blocked_write(value):
            entered.set()
            if not release.wait(10):
                raise RuntimeError('Test did not release the pending write')
            return original(value)

        def post():
            try:
                payload = {'kind': 'income', 'amount': '42.50', 'occurred_at': '2026-10-22 12:30:00',
                           'account_id': 'cash', 'category_id': 'sales', 'purpose': 'Synthetic shutdown',
                           'idempotency_key': 'shutdown-inflight'}
                request = urllib.request.Request(service.url + '/api/transactions',
                    data=json.dumps(payload).encode(),
                    headers={'Content-Type': 'application/json', 'X-Local-CSRF': csrf})
                with client.open(request, timeout=10) as response:
                    results.append(json.load(response))
            except BaseException as exc:
                errors.append(exc)

        def stop():
            try:
                service.stop()
            except BaseException as exc:
                errors.append(exc)
            finally:
                stopped.set()

        with patch.object(application.store, 'add_transaction', side_effect=blocked_write):
            request_thread = threading.Thread(target=post)
            stop_thread = threading.Thread(target=stop)
            request_thread.start()
            try:
                self.assertTrue(entered.wait(5), 'Request never reached the write')
                stop_thread.start()
                self.assertFalse(stopped.wait(0.3), 'Safe stop returned before the pending write')
                with self.assertRaisesRegex(Problem, '已经打开'):
                    with DataDirectoryLock(self.directory):
                        pass
            finally:
                release.set()
                request_thread.join(10)
                if stop_thread.ident is not None:
                    stop_thread.join(10)
        self.assertFalse(request_thread.is_alive())
        self.assertTrue(stopped.is_set())
        self.assertFalse(errors, errors)
        self.assertTrue(results[0]['backup_ok'])
        self.assertEqual(application.store.summary('2026-10')['income_cents'], 4250)
        self.assertTrue(application.store.backup_status()['database_backups'])
        with DataDirectoryLock(self.directory):
            pass

    def test_source_launcher_cannot_open_active_desktop_ledger(self):
        service = self.service().start()
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, str(root / 'run.py'), '--data-dir', str(self.directory),
                                 '--port', '0', '--host', '127.0.0.1', '--no-browser'],
                                cwd=root, timeout=15, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertTrue(service.running)

    def test_directory_lock_is_held_across_processes_and_released_after_stop(self):
        service = self.service().start()
        child = (
            'import sys\nfrom localledger.desktop import DataDirectoryLock\n'
            'from localledger.store import Problem\n'
            'try:\n    lock=DataDirectoryLock(sys.argv[1]); lock.acquire()\n'
            'except Problem:\n    sys.exit(2)\n'
            'lock.release()\n'
        )
        command = [sys.executable, '-c', child, str(self.directory)]
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(subprocess.run(command, cwd=root, timeout=15, capture_output=True).returncode, 2)
        service.stop()
        result = subprocess.run(command, cwd=root, timeout=15, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_port_conflict_never_attaches_to_other_ledger(self):
        first = self.service().start()
        other = Path(self.temporary.name) / '另一账本'
        second = self.service(other, port=first.server.server_address[1])
        with self.assertRaises((Problem, OSError)):
            second.start()
        self.assertFalse((other / 'ledger.sqlite3').exists())
        self.assertEqual(second.url, '')
        self.assertTrue(first.running)
        with DataDirectoryLock(other):
            pass

    def test_failed_bind_cleans_up_application_and_directory_lock(self):
        service = self.service()
        with patch('localledger.desktop.make_server', side_effect=OSError(errno.EADDRINUSE, 'occupied')):
            with self.assertRaisesRegex(Problem, '端口'):
                service.start()
        self.assertIsNone(service.application)
        self.assertFalse(service.running)
        with DataDirectoryLock(self.directory):
            pass
        service.start()
        self.assertTrue(service.running)

    def test_trial_mode_rejects_formal_directory(self):
        service = self.service().start()
        original = (self.directory / 'ledger.sqlite3').read_bytes()
        service.stop()
        with self.assertRaises(Problem):
            self.service(test_mode=True).start()
        self.assertEqual((self.directory / 'ledger.sqlite3').read_bytes(), original)

    def test_fresh_trial_starts_and_restart_preserves_existing_trial(self):
        self.assertFalse(self.directory.exists())
        service = self.service(test_mode=True).start()
        self.assertTrue(service.application.test_mode)
        store = service.application.store
        self.assertGreater(len(store.transactions()), 0)
        initial_transactions = store.transactions()
        initial_products = store.products()
        ledger_id = store.ledger_id
        service.stop()
        restarted = self.service(test_mode=True).start()
        self.assertEqual(restarted.application.store.ledger_id, ledger_id)
        self.assertEqual(restarted.application.store.transactions(), initial_transactions)
        self.assertEqual(restarted.application.store.products(), initial_products)

    def test_lock_does_not_create_data_directory_before_initialization(self):
        with DataDirectoryLock(self.directory):
            self.assertFalse(self.directory.exists())
            with self.assertRaisesRegex(Problem, '已经打开'):
                with DataDirectoryLock(self.directory):
                    pass

    def test_default_trial_directory_and_port_are_separate(self):
        with patch('localledger.desktop.program_directory', return_value=Path(self.temporary.name)):
            formal = parse_arguments([])
            trial = parse_arguments(['--test-mode'])
        self.assertEqual(Path(formal.data_dir).name, 'data')
        self.assertEqual(Path(trial.data_dir).name, 'test-data')
        self.assertNotEqual(formal.port, trial.port)

    def test_app_window_uses_one_literal_url_argument(self):
        with patch('localledger.desktop.find_app_browser', return_value='C:/Program Files/Edge/msedge.exe'), \
                patch('localledger.desktop.subprocess.Popen') as process:
            open_app_window('http://127.0.0.1:8765')
            self.assertEqual(process.call_args.args[0], ['C:/Program Files/Edge/msedge.exe', '--app=http://127.0.0.1:8765'])
            self.assertFalse(process.call_args.kwargs.get('shell', False))
            with self.assertRaises(ValueError):
                open_app_window('https://example.com')


class RestoreLauncherCase(unittest.TestCase):
    def test_quotes_cmd_metacharacters_and_rejects_multiline_paths(self):
        self.assertEqual(_cmd_argument('D:\\中文 空格\\%TEMP% & !账本'), '"D:\\中文 空格\\%%TEMP%% & !账本"')
        for value in ['a\nb', 'a\rb', 'a\0b', 'a"b']:
            with self.assertRaises(ValueError):
                _cmd_argument(value)

    def test_frozen_launcher_is_exclusive_and_stays_in_restored_folder(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / '恢复 %TEMP% & !'
            directory.mkdir()
            executable = Path(temporary) / '软件 50% & !' / 'XiaoGuoLedger.exe'
            with patch('localledger.desktop.sys.executable', str(executable)), \
                    patch('localledger.desktop.sys.frozen', True, create=True):
                launcher = create_restore_launcher(directory)
                contents = launcher.read_text(encoding='utf-8')
                self.assertEqual(launcher.parent, directory.resolve())
                self.assertIn('setlocal DisableDelayedExpansion', contents)
                self.assertIn('%%TEMP%%', contents)
                self.assertIn(_cmd_argument(executable.resolve()), contents)
                self.assertNotIn('desktop.py', contents)
                with self.assertRaises(FileExistsError):
                    create_restore_launcher(directory)
                self.assertEqual(launcher.read_text(encoding='utf-8'), contents)

    @unittest.skipUnless(os.name == 'nt', 'Exercises Windows command processing')
    def test_source_launcher_preserves_real_special_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / '软件 %TEMP% & ! 中文'
            root.mkdir()
            directory = root / '恢复 %PATH% & !'
            directory.mkdir()
            (root / 'desktop.py').write_text(
                "import json, sys\nfrom pathlib import Path\n"
                "Path(__file__).with_name('received.json').write_text(json.dumps(sys.argv[1:]), encoding='utf-8')\n",
                encoding='utf-8')
            with patch('localledger.desktop.program_directory', return_value=root), \
                    patch('localledger.desktop.sys.frozen', False, create=True):
                launcher = create_restore_launcher(directory)
            # % characters in the launcher path itself must not pass through cmd /c.
            # Set cwd via the process API and invoke the simple literal filename.
            result = subprocess.run(['cmd.exe', '/d', '/c', launcher.name], cwd=directory,
                                    timeout=10, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            received = root / 'received.json'
            deadline = time.monotonic() + 10
            while not received.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(received.exists(), 'Launcher did not run the intended Python script')
            self.assertEqual(json.loads(received.read_text(encoding='utf-8')), ['--data-dir', str(directory.resolve())])

    def test_launcher_failure_does_not_report_successful_recovery_as_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = Store(root / 'original')
            archive = original.backup_file()
            destination = root / 'recovered'
            with patch('localledger.desktop.create_restore_launcher', side_effect=PermissionError('read only')):
                result = restore_for_desktop(archive, destination)
            self.assertTrue((destination / 'ledger.sqlite3').is_file())
            self.assertEqual(result['directory'], destination.resolve())
            self.assertIsNone(result['launcher'])
            self.assertEqual(result['launcher_warning'], 'read only')


if __name__ == '__main__':
    unittest.main()
