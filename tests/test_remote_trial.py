import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

from localledger.access import set_password
from scripts.remote_trial import tunnel_url


class RemoteTrialCase(unittest.TestCase):
    def test_only_accepts_https_quick_tunnel_hostname(self):
        self.assertEqual(tunnel_url('INFO | https://test-store-123.trycloudflare.com |'), 'https://test-store-123.trycloudflare.com')
        self.assertIsNone(tunnel_url('http://test.trycloudflare.com'))
        self.assertIsNone(tunnel_url('https://test.trycloudflare.com.evil.example'))

    @unittest.skipIf(os.name == 'nt', 'POSIX subprocess simulation; Windows startup needs device verification')
    def test_launcher_starts_password_app_and_cleans_up_children(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / 'data'
            set_password(data, 'simulation-password-123')
            fake = root / 'cloudflared'
            fake.write_text('#!' + sys.executable + '\nimport time\nprint("https://test-store.trycloudflare.com",flush=True)\nwhile True: time.sleep(1)\n')
            fake.chmod(0o700)
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 0))
                port = probe.getsockname()[1]
            log = root / 'output.txt'
            script = Path(__file__).resolve().parents[1] / 'scripts/remote_trial.py'
            with log.open('w') as output:
                process = subprocess.Popen([sys.executable, str(script), '--data-dir', str(data), '--cloudflared', str(fake), '--port', str(port)], stdout=output, stderr=subprocess.STDOUT)
                try:
                    deadline = time.monotonic() + 15
                    while True:
                        try:
                            req = urllib.request.Request(f'http://127.0.0.1:{port}/api/status', headers={'Host': 'test-store.trycloudflare.com', 'X-Forwarded-Proto': 'https'})
                            with urllib.request.urlopen(req, timeout=1) as response:
                                status = json.load(response)
                            break
                        except (OSError, urllib.error.URLError):
                            if process.poll() is not None or time.monotonic() >= deadline:
                                self.fail(log.read_text())
                            time.sleep(0.05)
                    self.assertEqual(status['auth_mode'], 'password')
                    self.assertFalse(status['authenticated'])
                finally:
                    process.send_signal(signal.SIGINT)
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                self.assertEqual(process.returncode, 0)
                with socket.socket() as check:
                    self.assertNotEqual(check.connect_ex(('127.0.0.1', port)), 0)
