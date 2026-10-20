"""Exercise trial ledger interactions in Chromium, using isolated synthetic data."""
from pathlib import Path
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from localledger.server import Application, make_server
from playwright.sync_api import sync_playwright, expect


def main():
    output = Path('output/playwright/interactions')
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ledger-interactions-') as directory:
        app = Application(directory, code='123456', test_mode=True)
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
                expect(page.get_by_text('本地测试版 · 模拟账本', exact=True)).to_be_visible()
                page.locator('[data-page=transactions]').click()
                page.get_by_role('button', name='记一笔', exact=True).click()
                dialog = page.get_by_role('dialog')
                expect(dialog.locator('[data-kind]')).to_have_count(4)
                dialog.get_by_role('button', name='保存并备份', exact=True).click()
                expect(dialog.locator('.form-error')).to_contain_text('金额')
                expect(dialog.get_by_label('金额（元）', exact=True)).to_be_focused()
                dialog.get_by_label('金额（元）', exact=True).fill('1.005')
                dialog.get_by_role('button', name='保存并备份', exact=True).click()
                expect(dialog.locator('.form-error')).to_contain_text('最多两位小数')
                dialog.get_by_label('金额（元）', exact=True).fill('12.34')
                dialog.get_by_label('商品或用途', exact=True).fill('交互回归采购')
                dialog.get_by_label('付款账户', exact=True).select_option('cash')
                dialog.get_by_label('类目', exact=True).select_option('ingredients')
                dialog.get_by_label('记住这次用途对应的分类', exact=True).check()
                dialog.get_by_role('button', name='收入', exact=True).click()
                expect(dialog.get_by_label('金额（元）', exact=True)).to_have_value('12.34')
                dialog.get_by_label('金额（元）', exact=True).fill('23.45')
                dialog.get_by_label('类目', exact=True).select_option('other_income')
                dialog.get_by_role('button', name='转账', exact=True).click()
                dialog.get_by_label('转入账户', exact=True).select_option('bank')
                dialog.get_by_role('button', name='支出', exact=True).click()
                expect(dialog.get_by_label('金额（元）', exact=True)).to_have_value('23.45')
                expect(dialog.get_by_label('商品或用途', exact=True)).to_have_value('交互回归采购')
                expect(dialog.get_by_label('类目', exact=True)).to_have_value('ingredients')
                expect(dialog.get_by_label('记住这次用途对应的分类', exact=True)).to_be_checked()
                dialog.get_by_role('button', name='收入', exact=True).click()
                expect(dialog.get_by_label('类目', exact=True)).to_have_value('other_income')
                dialog.get_by_role('button', name='转账', exact=True).click()
                expect(dialog.get_by_label('转入账户', exact=True)).to_have_value('bank')
                dialog.get_by_role('button', name='支出', exact=True).click()
                # Escape, cancel and backdrop must all protect a dirty form.
                confirmations = []
                def decline(item):
                    confirmations.append(item.message)
                    item.dismiss()
                page.on('dialog', decline)
                page.keyboard.press('Escape')
                dialog.get_by_role('button', name='取消', exact=True).click()
                page.locator('.modal-backdrop').click(position={'x': 3, 'y': 3})
                expect(dialog).to_be_visible()
                assert len(confirmations) == 3
                page.remove_listener('dialog', decline)
                # Keyboard cannot tab into the page behind the modal.
                dialog.get_by_role('button', name='保存并备份', exact=True).focus()
                page.keyboard.press('Tab')
                expect(dialog.get_by_role('button', name='关闭', exact=True)).to_be_focused()
                page.keyboard.press('Shift+Tab')
                expect(dialog.get_by_role('button', name='保存并备份', exact=True)).to_be_focused()
                assert page.locator('#app').evaluate('(e)=>e.inert')
                # Lose a response after the server commits, then retry exactly once.
                def lose_response(route):
                    if route.request.method != 'POST':
                        route.continue_()
                        return
                    response = route.fetch()
                    assert response.ok
                    route.abort('failed')
                page.route('**/api/transactions', lose_response)
                dialog.get_by_role('button', name='保存并备份', exact=True).click()
                expect(dialog.locator('.form-error')).to_contain_text('保存结果尚未确认')
                assert len(app.store.transactions()) == 4
                expect(dialog.get_by_label('金额（元）', exact=True)).to_be_disabled()
                page.unroute('**/api/transactions', lose_response)
                dialog.get_by_role('button', name='核实上次保存并备份', exact=True).click()
                expect(dialog).not_to_be_visible()
                assert len(app.store.transactions()) == 4
                assert sum(x['amount_cents'] for x in app.store.transactions() if x['kind'] == 'expense') == 9185
                expect(page.get_by_role('row').filter(has_text='交互回归采购')).to_contain_text('23.45')
                assert not page.locator('#app').evaluate('(e)=>e.inert')
                # Search state survives posting; delayed search cannot replace another page.
                page.get_by_label('搜索账目').fill('交互回归采购')
                expect(page.locator('[data-tx]')).to_have_count(1)
                page.get_by_label('收支类型').select_option('expense')
                page.get_by_role('button', name='记一笔', exact=True).click()
                page.get_by_label('金额（元）', exact=True).fill('2.00')
                page.get_by_label('商品或用途', exact=True).fill('交互回归采购二')
                page.get_by_label('类目', exact=True).select_option('ingredients')
                page.get_by_label('付款账户', exact=True).select_option('cash')
                page.get_by_role('button', name='保存并备份', exact=True).click()
                expect(dialog).not_to_be_visible()
                expect(page.get_by_label('搜索账目')).to_have_value('交互回归采购')
                expect(page.get_by_label('收支类型')).to_have_value('expense')
                expect(page.locator('[data-tx]')).to_have_count(2)
                page.get_by_label('搜索账目').fill('离开页面前输入')
                page.locator('[data-page=inventory]').click()
                page.wait_for_timeout(600)  # Must exceed the old search debounce.
                expect(page.get_by_role('heading', name='库存管理', exact=True)).to_be_visible()
                # At phone size and a short keyboard-like viewport all fields remain reachable.
                page.set_viewport_size({'width': 390, 'height': 520})
                page.locator('[data-page=transactions]').click()
                page.get_by_role('button', name='记一笔', exact=True).click()
                dialog.get_by_label('金额（元）', exact=True).fill('3.00')
                dialog.get_by_label('商品或用途', exact=True).fill('窄屏交互测试')
                dialog.get_by_label('类目', exact=True).select_option('supplies')
                dialog.get_by_label('付款账户', exact=True).select_option('cash')
                page.screenshot(path=str(output/'mobile-entry.png'), full_page=True)
                dialog.get_by_role('button', name='保存并备份', exact=True).click()
                expect(dialog).not_to_be_visible()
                assert len(app.store.transactions()) == 6
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.screenshot(path=str(output/'mobile-ledger.png'), full_page=True)
                page.set_viewport_size({'width': 1440, 'height': 1000})
                page.screenshot(path=str(output/'desktop-ledger.png'), full_page=True)
                assert not errors, errors
                browser.close()
                print('PASS: trial login, entry button, explicit validation, preserved type fields, dirty-close protection, focus trap, committed/lost-response retry without duplication, filters, search/navigation race, short mobile viewport')
        finally:
            server.shutdown()
            server.server_close()
            app.close()
            worker.join()


if __name__ == '__main__':
    main()
