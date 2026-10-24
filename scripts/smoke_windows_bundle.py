"""Run a frozen Windows bundle against disposable synthetic data."""
import argparse
import http.cookiejar
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from localledger.access import set_password
from localledger.restore import restore_backup
from localledger.store import Store
from localledger.trial import prepare_trial
from playwright.sync_api import sync_playwright, expect
from scripts.browser_test_support import launch_browser


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('bundle', type=Path)
    parser.add_argument('--screenshots', type=Path, default=Path('output/playwright/windows'))
    args = parser.parse_args()
    executable = args.bundle.resolve() / 'XiaoGuoLedger.exe'
    assert executable.is_file()
    args.screenshots.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ledger-frozen-synthetic-') as temp:
        root = Path(temp)
        directory = prepare_trial(root / '中文练习 账本')
        set_password(directory, 'synthetic-only-verify-123')
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        url = f'http://127.0.0.1:{port}'
        client = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        process = None
        baseline = Store(directory).summary(None)['income_cents']
        try:
            for cycle in range(2):
                process = subprocess.Popen([str(executable), '--data-dir', str(directory),
                                            '--host', '127.0.0.1', '--port', str(port), '--no-auto-open'])
                deadline = time.monotonic() + 20
                while True:
                    try:
                        with client.open(url + '/api/status', timeout=1) as response:
                            status = json.load(response)
                        assert status['version'] == '0.4.3' and status['test_mode']
                        break
                    except OSError:
                        if process.poll() is not None or time.monotonic() > deadline:
                            raise RuntimeError('Frozen application did not start')
                        time.sleep(0.2)
                with sync_playwright() as playwright:
                    browser = launch_browser(playwright)
                    try:
                        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                        page.goto(url)
                        page.get_by_label('访问密码', exact=True).fill('synthetic-only-verify-123')
                        page.get_by_role('button', name='进入账本').click()
                        expect(page.locator('[data-page=inventory]')).to_be_visible()
                        if cycle == 0:
                            page.locator('#content [data-new-entry]').click()
                            page.locator('.kind-tabs [data-kind=income]').click()
                            form = page.locator('#entry-form')
                            form.get_by_label('金额（元）', exact=True).fill('18.00')
                            form.get_by_label('商品或用途', exact=True).fill('便携版验收鸡肉小锅菜（模拟）')
                            form.get_by_label('类目', exact=True).select_option('sales')
                            form.get_by_label('收款账户', exact=True).select_option('cash')
                            form.get_by_role('button', name='保存并备份', exact=True).click()
                            expect(page.get_by_role('dialog')).not_to_be_visible()
                        page.locator('[data-page=transactions]').click()
                        expect(page.get_by_text('便携版验收鸡肉小锅菜（模拟）', exact=True)).to_be_visible()
                        page.screenshot(path=str(args.screenshots / 'windows-ledger-synthetic.png'), full_page=True)
                        phone = browser.new_page(viewport={'width': 390, 'height': 844})
                        phone.goto(url)
                        phone.get_by_label('访问密码', exact=True).fill('synthetic-only-verify-123')
                        phone.get_by_role('button', name='进入账本').click()
                        phone.locator('[data-page=transactions]').click()
                        expect(phone.get_by_text('便携版验收鸡肉小锅菜（模拟）', exact=True)).to_be_visible()
                        assert phone.evaluate('document.documentElement.scrollWidth <= innerWidth')
                        for amount in phone.locator('td.amount').all():
                            box = amount.bounding_box()
                            assert box and box['x'] + box['width'] <= 390, box
                        phone.screenshot(path=str(args.screenshots / 'phone-ledger-synthetic.png'), full_page=True)
                        response = page.request.get(url + '/api/backup.zip')
                        assert response.ok
                        archive = root / f'backup-{cycle}.zip'
                        archive.write_bytes(response.body())
                        destination = root / f'restored-{cycle}'
                        restore_backup(archive, destination)
                        assert Store(destination).summary(None)['income_cents'] == baseline + 1800
                    finally:
                        browser.close()
                # No requests remain; terminate only this disposable test process.
                # Graceful controller shutdown with active writes is covered by test_desktop.
                process.terminate()
                process.wait(timeout=10)
                process = None
            print('PASS: frozen EXE starts in Chinese path; two browser sessions share data; saved entries survive restart; exported ZIP restores correctly')
        finally:
            if process is not None:
                process.terminate()
                process.wait(timeout=10)


if __name__ == '__main__':
    main()
