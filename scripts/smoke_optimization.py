"""Real browser checks for lost checkout responses, draft recovery and daily reconciliation."""
from pathlib import Path
import sys
import tempfile
import threading
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from localledger.server import Application, make_server
from playwright.sync_api import sync_playwright, expect


def main():
    output = Path('output/playwright/optimization')
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ledger-optimization-') as directory:
        app = Application(directory, code='123456')
        raw = app.store.add_product({'name': '鸡肉（模拟）', 'unit': '千克'})['id']
        app.store.add_movement({'product_id': raw, 'kind': 'in', 'quantity': 5, 'occurred_at': '2026-10-19 10:00', 'idempotency_key': uuid4().hex})
        app.store.save_menu({'name': '猪肚鸡（模拟）', 'barcode': '10001', 'price': '38.50', 'stock_mode': 'recipe', 'recipe': [{'product_id': raw, 'quantity': '0.4'}]})
        server = make_server(app, port=0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox'])
                context = browser.new_context(viewport={'width': 1440, 'height': 1000})
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{server.server_address[1]}')
                page.get_by_label('六位连接码').fill('123456')
                page.get_by_role('button', name='进入账本').click()
                page.locator('[data-page=pos]').click()
                page.locator('[data-dish]').click()
                page.reload()
                page.locator('[data-page=pos]').click()
                expect(page.locator('.cart-total')).to_contain_text('38.50')
                page.locator('[data-dish]').click()
                expect(page.locator('.cart-total')).to_contain_text('77.00')
                def lose_response(route):
                    response = route.fetch()
                    assert response.status == 200
                    route.abort('failed')
                page.route('**/api/checkout', lose_response, times=1)
                page.get_by_role('button', name='核实收款并开单').click()
                dialog = page.get_by_role('dialog')
                dialog.get_by_label('收款时间').fill('2026-10-19T12:00')
                dialog.get_by_label('已核实收到 ¥77.00，确认记账并扣减配方原料').check()
                dialog.get_by_role('button', name='保存销售并生成小票').click()
                expect(dialog.locator('.form-error')).to_contain_text('收银结果待核实')
                assert len(app.store.orders()) == 1
                original = app.store.orders()[0]['id']
                page.reload()
                page.locator('[data-page=pos]').click()
                page.get_by_role('button', name='核实上次收银结果').click()
                page.get_by_role('button', name='核实原请求并显示小票').click()
                expect(page.locator('.ticket-total')).to_have_text('合计 ¥77.00')
                assert len(app.store.orders()) == 1 and app.store.orders()[0]['id'] == original
                assert app.store.products()[0]['stock_milli'] == 4200
                assert app.store.summary('2026-10')['income_cents'] == 7700
                page.get_by_role('button', name='关闭', exact=True).click()
                page.locator('[data-page=overview]').click()
                page.get_by_role('button', name='每日核对', exact=True).click()
                expect(page.get_by_role('heading', name='每日总额核对')).to_be_visible()
                page.get_by_label('核对日期').fill('2026-10-19')
                # Native date change fires on blur; re-open deterministically if already the same day.
                page.get_by_label('核对账户').focus()
                form = page.locator('[data-check-form=income]')
                form.get_by_label('净收入核对总额（元）').fill('77.00')
                form.get_by_role('button', name='保存收入核对额').click()
                expect(page.get_by_text('已对平', exact=True)).to_be_visible()
                assert len(app.store.transactions()) == 1
                assert app.store.daily_check('2026-10-19')['checks'][0]['matched']
                app.store.add_transaction({'kind': 'income', 'amount': '2', 'category_id': 'sales', 'account_id': 'cash', 'occurred_at': '2026-10-19 13:00', 'idempotency_key': uuid4().hex})
                page.get_by_role('button', name='关闭', exact=True).click()
                page.get_by_role('button', name='每日核对', exact=True).click()
                page.get_by_label('核对日期').fill('2026-10-19')
                page.get_by_label('核对账户').focus()
                expect(page.get_by_text('存在差额', exact=True)).to_be_visible()
                assert app.store.daily_check('2026-10-19')['checks'][0]['difference_cents'] == 200
                page.screenshot(path=str(output/'daily-check-desktop.png'), full_page=True)
                page.set_viewport_size({'width': 390, 'height': 844})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.screenshot(path=str(output/'daily-check-mobile.png'), full_page=True)
                assert not errors, errors
                browser.close()
                print('PASS: persisted cart, server-committed/lost-response recovery, no duplicate sale/stock, daily reconciliation and changing differences, mobile width')
        finally:
            server.shutdown()
            server.server_close()
            app.close()
            worker.join()


if __name__ == '__main__':
    main()
