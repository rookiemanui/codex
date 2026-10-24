"""Verify manual multi-entry review and real backup download/restore, not OCR accuracy."""
import base64
from contextlib import contextmanager
from datetime import date
from pathlib import Path
import sys
import tempfile
import threading
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from localledger.server import Application, make_server
from playwright.sync_api import sync_playwright, expect

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jN1sAAAAASUVORK5CYII=')


@contextmanager
def running(directory, **options):
    app = Application(directory, code='123456', **options)
    server = make_server(app, port=0)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield app, f'http://127.0.0.1:{server.server_address[1]}'
    finally:
        server.shutdown()
        server.server_close()
        app.close()
        worker.join()


def login(page, url):
    page.goto(url)
    expect(page.get_by_text('本地测试版 · 模拟账本', exact=True)).to_be_visible()


def main():
    output = Path('output/playwright/review-recovery')
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='review-recovery-') as directory, sync_playwright() as p:
        root = Path(directory)
        browser = p.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox'])
        errors = []
        with running(root / 'source', test_mode=True) as (app, url):
            # Explicitly seed manual review drafts; this does not substitute for OCR testing.
            receipt = app.store.save_receipt('人工审核流程模拟图.png', PNG)['id']
            day = date.today().isoformat()
            drafts = [{'kind': 'expense', 'amount': '1.23', 'purpose': '识别初值' + str(i),
                       'occurred_at': day + ' 10:00', 'account_id': 'cash', 'category_id': 'ingredients'} for i in range(2)]
            app.store.set_ocr(receipt, '模拟人工审核状态，不是OCR准确率测试', drafts)
            context = browser.new_context(viewport={'width': 1440, 'height': 1000}, accept_downloads=True)
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            login(page, url)
            page.locator('[data-page=receipts]').click()
            page.locator('.receipt-tile').click()
            forms = page.locator('form[data-draft]')
            expect(forms).to_have_count(2)
            second = forms.nth(1)
            expect(second.locator('[name=kind] option')).to_have_count(4)
            second.get_by_label('商品或用途', exact=True).fill('人工核对第二笔')
            second.get_by_label('金额（元）', exact=True).fill('4.56')
            second.get_by_label('类目', exact=True).select_option('supplies')
            second.get_by_label('记住这次用途对应的分类', exact=True).check()
            second.get_by_label('收支方向').select_option('income')
            second.get_by_label('类目', exact=True).select_option('other_income')
            second.get_by_label('收支方向').select_option('transfer')
            second.get_by_label('转入账户', exact=True).select_option('bank')
            second.get_by_label('收支方向').select_option('expense')
            expect(second.get_by_label('类目', exact=True)).to_have_value('supplies')
            expect(second.get_by_label('记住这次用途对应的分类', exact=True)).to_be_checked()
            second.get_by_label('收支方向').select_option('income')
            expect(second.get_by_label('类目', exact=True)).to_have_value('other_income')
            second.get_by_label('收支方向').select_option('transfer')
            expect(second.get_by_label('转入账户', exact=True)).to_have_value('bank')
            second.get_by_label('收支方向').select_option('expense')
            first = forms.first
            first.get_by_label('金额（元）', exact=True).fill('1.005')
            first.get_by_role('button', name='确认本笔并入账').click()
            expect(first.locator('.form-error')).to_contain_text('最多两位小数')
            first.get_by_label('金额（元）', exact=True).fill('1.23')
            first.get_by_label('商品或用途', exact=True).fill('人工核对第一笔')
            def lose_response(route):
                response = route.fetch()
                assert response.ok
                route.abort('failed')
            page.route('**/api/transactions', lose_response, times=1)
            first.get_by_role('button', name='确认本笔并入账').click()
            expect(first.locator('.form-error')).not_to_be_empty()
            assert len(app.store.transactions()) == 4
            first.get_by_role('button', name='确认本笔并入账').click()
            expect(page.get_by_text('本笔已经入账并备份', exact=False)).to_be_visible()
            expect(forms).to_have_count(1)
            second = forms.first
            expect(second.get_by_label('商品或用途', exact=True)).to_have_value('人工核对第二笔')
            expect(second.get_by_label('金额（元）', exact=True)).to_have_value('4.56')
            expect(second.get_by_label('类目', exact=True)).to_have_value('supplies')
            expect(second.get_by_label('记住这次用途对应的分类', exact=True)).to_be_checked()
            assert len(app.store.transactions()) == 4
            page.get_by_role('button', name='返回截图', exact=True).click()
            page.locator('.receipt-tile').click()
            expect(forms.first.get_by_label('商品或用途', exact=True)).to_have_value('人工核对第二笔')
            # A real beforeunload dialog must let the user cancel a refresh.
            with page.expect_event('dialog') as pending_dialog:
                page.evaluate('setTimeout(() => location.reload(), 0)')
            assert pending_dialog.value.type == 'beforeunload'
            pending_dialog.value.dismiss()
            expect(forms.first.get_by_label('金额（元）', exact=True)).to_have_value('4.56')
            forms.first.get_by_role('button', name='确认本笔并入账').click()
            expect(page.get_by_text('本笔已经入账并备份', exact=False)).to_have_count(2)
            assert len(app.store.transactions()) == 5
            print('PASS: two manual drafts, type/category memory, inline validation, saved/lost-response retry, other edits survive posting/navigation, refresh protection', flush=True)
            # Another browser session writes to the same ledger.
            phone_context = browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
            phone = phone_context.new_page()
            phone.on('pageerror', lambda error: errors.append(str(error)))
            login(phone, url)
            phone.get_by_role('button', name='记一笔', exact=True).click()
            dialog = phone.get_by_role('dialog')
            dialog.get_by_role('button', name='收入', exact=True).click()
            dialog.get_by_label('金额（元）', exact=True).fill('19.99')
            dialog.get_by_label('商品或用途', exact=True).fill('手机会话新增测试收入')
            dialog.get_by_label('类目', exact=True).select_option('sales')
            dialog.get_by_label('收款账户', exact=True).select_option('cash')
            dialog.get_by_role('button', name='保存并备份', exact=True).click()
            expect(dialog).not_to_be_visible()
            page.locator('[data-page=transactions]').click()
            expect(page.get_by_role('row').filter(has_text='手机会话新增测试收入')).to_contain_text('19.99')
            assert app.store.summary(None)['expense_cents'] == 7419
            assert app.store.summary(None)['income_cents'] == 13999
            # A late classification suggestion may not override an explicit choice.
            page.get_by_role('button', name='记一笔', exact=True).click()
            dialog = page.get_by_role('dialog')
            held = []
            page.route('**/api/rules', lambda route: held.append(route))
            dialog.get_by_label('商品或用途', exact=True).fill('人工核对第二笔')
            with page.expect_request('**/api/rules'):
                dialog.get_by_label('金额（元）', exact=True).focus()
            dialog.get_by_label('类目', exact=True).select_option('ingredients')
            assert held
            for route in held:
                response = route.fetch()
                route.fulfill(response=response)
            page.unroute('**/api/rules')
            page.wait_for_timeout(100)
            expect(dialog.get_by_label('类目', exact=True)).to_have_value('ingredients')
            page.once('dialog', lambda item: item.accept())
            dialog.get_by_role('button', name='取消', exact=True).click()
            print('PASS: independent phone session shares ledger, totals match, late rule does not overwrite chosen category', flush=True)
            page.locator('[data-page=settings]').click()
            with page.expect_download() as download:
                page.get_by_role('button', name='下载完整备份', exact=True).click()
            backup = root / 'downloaded-backup.zip'
            download.value.save_as(backup)
            expected = {name: getattr(app.store, name)() for name in ['transactions', 'menu', 'products', 'orders', 'shop', 'rules', 'receipts']}
            with zipfile.ZipFile(backup) as archive:
                assert '.test-ledger.json' in archive.namelist()
                assert 'access.json' not in archive.namelist()
                archive.extractall(root / 'restored')
            with running(root / 'restored') as (restored, restored_url):
                assert restored.test_mode
                assert restored.store.ledger_id == app.store.ledger_id
                for name, rows in expected.items():
                    assert getattr(restored.store, name)() == rows, name
                assert restored.store.receipt_path(receipt).read_bytes() == PNG
                status = restored.store.backup_status()
                assert status['event_count'] == status['record_backup_count']
                assert status['receipt_backup_count'] == 1
                restored_context = browser.new_context(viewport={'width': 1440, 'height': 1000})
                restored_page = restored_context.new_page()
                restored_page.on('pageerror', lambda error: errors.append(str(error)))
                login(restored_page, restored_url)
                restored_page.locator('[data-page=transactions]').click()
                expect(restored_page.get_by_role('row').filter(has_text='人工核对第二笔')).to_contain_text('4.56')
                restored_page.screenshot(path=str(output / 'restored-ledger.png'), full_page=True)
            assert not errors, errors
            print('PASS: real HTTP backup download, fresh restored server/login, test label, exact ledger/stock/recipes/rules/original bytes and independent receipt copy', flush=True)
        browser.close()


if __name__ == '__main__':
    main()
