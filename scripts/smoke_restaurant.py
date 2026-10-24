"""Real browser restaurant checkout/printing/phone workflow; synthetic data only."""
from pathlib import Path
import sys
import tempfile
import threading
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from localledger.server import Application, make_server
from playwright.sync_api import sync_playwright, expect
from scripts.browser_test_support import launch_browser


def main():
    output = Path('.test-data/restaurant')
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        app = Application(directory, code='123456')
        raw = app.store.add_product({'name': '鸡肉（模拟）', 'unit': '千克'})['id']
        app.store.add_movement({'product_id': raw, 'kind': 'in', 'quantity': '5', 'occurred_at': '2026-10-19 10:00', 'idempotency_key': uuid4().hex})
        server = make_server(app, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        errors = []
        try:
            with sync_playwright() as p:
                browser = launch_browser(p)
                context = browser.new_context(viewport={'width': 1440, 'height': 1000})
                page = context.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                url = f'http://127.0.0.1:{server.server_address[1]}'
                page.goto(url)
                page.get_by_label('六位连接码').fill('123456')
                page.get_by_role('button', name='进入账本').click()
                page.locator('[data-page=pos]').click()
                page.get_by_role('button', name='菜品与配方', exact=True).click()
                page.get_by_role('button', name='新增菜品', exact=True).click()
                dialog = page.get_by_role('dialog')
                dialog.get_by_label('菜品名称', exact=True).fill('猪肚鸡（模拟）')
                dialog.get_by_label('菜品条码 / 编码', exact=True).fill('10001')
                dialog.get_by_label('每份售价（元）', exact=True).fill('38.50')
                dialog.get_by_label('每份用量', exact=True).fill('0.4')
                dialog.get_by_role('button', name='保存菜品与配方').click()
                expect(page.get_by_role('heading', name='菜品与配方', exact=True)).to_be_visible()
                page.get_by_role('button', name='关闭', exact=True).click()
                page.get_by_label('扫描菜品条码 / 输入菜品编码').fill('10001')
                page.get_by_label('扫描菜品条码 / 输入菜品编码').press('Enter')
                page.get_by_role('button', name='增加猪肚鸡（模拟）').click()
                expect(page.locator('.cart-total')).to_contain_text('77.00')
                page.screenshot(path=str(output/'pos-desktop.png'), full_page=True)
                page.get_by_role('button', name='核实收款并开单').click()
                dialog = page.get_by_role('dialog')
                dialog.get_by_label('已核实收到 ¥77.00，确认记账并扣减配方原料').check()
                dialog.get_by_role('button', name='保存销售并生成小票').click()
                expect(page.get_by_role('heading', name='销售小票', exact=True).first).to_be_visible()
                expect(page.locator('.ticket-total')).to_have_text('合计 ¥77.00')
                # Exercise the print button without a system print dialog, then render the print media to PDF.
                page.evaluate('window.print = () => { window.printInvoked = true; }')
                page.get_by_role('button', name='打印 / 保存 PDF').click()
                assert page.evaluate('window.printInvoked')
                page.emulate_media(media='print')
                assert page.locator('#app').evaluate('(e) => getComputedStyle(e).display') == 'none'
                assert page.locator('.sale-ticket').is_visible()
                page.pdf(path=str(output/'ticket-80mm.pdf'), width='80mm', height='230mm', print_background=True)
                page.emulate_media(media='screen')
                page.locator('#ticket-width').select_option('58')
                page.get_by_role('button', name='打印 / 保存 PDF').click()
                page.emulate_media(media='print')
                assert page.locator('#app').evaluate('(e) => getComputedStyle(e).display') == 'none'
                page.pdf(path=str(output/'ticket-58mm.pdf'), width='58mm', height='260mm')
                page.emulate_media(media='screen')
                page.screenshot(path=str(output/'ticket.png'), full_page=True)
                assert app.store.summary('2026-10')['income_cents'] == 7700
                assert app.store.products()[0]['stock_milli'] == 4200
                page.get_by_role('button', name='关闭', exact=True).click()
                page.get_by_role('button', name='销售小票', exact=True).last.click()
                page.get_by_role('button', name='退单', exact=True).click()
                page.get_by_label('退款原因').fill('模拟退款测试')
                page.get_by_label('已通过原收款方式退还款项，确认登记退单').check()
                page.get_by_role('button', name='登记退款与备份').click()
                expect(page.get_by_role('dialog')).not_to_be_visible()
                assert app.store.summary('2026-10')['income_cents'] == 0
                assert app.store.products()[0]['stock_milli'] == 4200
                mobile = browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
                phone = mobile.new_page()
                phone.on('pageerror', lambda error: errors.append(str(error)))
                phone.goto(url)
                phone.get_by_label('六位连接码').fill('123456')
                phone.get_by_role('button', name='进入账本').click()
                phone.locator('[data-page=pos]').click()
                expect(phone.get_by_role('button', name='猪肚鸡（模拟）', exact=False).first).to_be_visible()
                assert phone.evaluate('document.documentElement.scrollWidth <= innerWidth')
                phone.screenshot(path=str(output/'pos-mobile.png'), full_page=True)
                phone.get_by_role('button', name='销售小票', exact=True).click()
                expect(phone.get_by_text('已退单', exact=True)).to_be_visible()
                assert not errors, errors
                browser.close()
                print('PASS: browser recipe, barcode cart, atomic checkout, 58/80mm PDF, refund, phone shared ledger')
        finally:
            server.shutdown()
            server.server_close()
            app.close()
            thread.join()


if __name__ == '__main__':
    main()
