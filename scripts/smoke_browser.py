"""End-to-end checks with a real local server and Chromium, isolated from user data."""
import argparse
from pathlib import Path
import sys
import tempfile
import threading
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from localledger.server import Application, make_server
from playwright.sync_api import sync_playwright, expect


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--fixture', required=True)
    parser.add_argument('--output', default='.test-data/browser')
    parser.add_argument('--browser', default='/usr/bin/chromium')
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    fixture = Path(args.fixture).resolve()
    errors = []
    with tempfile.TemporaryDirectory(prefix='ledger-browser-') as directory:
        app = Application(directory, code='123456')
        server = make_server(app, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f'http://127.0.0.1:{server.server_address[1]}'
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=args.browser, headless=True, args=['--no-sandbox'])
                context = browser.new_context(viewport={'width': 1440, 'height': 1100}, accept_downloads=True)
                page = context.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(url)
                page.get_by_label('六位连接码').fill('123456')
                page.get_by_role('button', name='进入账本').click()
                expect(page.get_by_role('heading', name='店铺总览', exact=True)).to_be_visible()

                def add(amount, category, purpose, kind='expense'):
                    page.get_by_role('button', name='记一笔', exact=True).click()
                    dialog = page.get_by_role('dialog')
                    if kind == 'income':
                        dialog.get_by_role('button', name='收入', exact=True).click()
                    dialog.get_by_label('金额（元）', exact=True).fill(amount)
                    dialog.get_by_label('交易时间', exact=True).fill('2026-10-12T11:43:23')
                    dialog.get_by_label('商品或用途', exact=True).fill(purpose)
                    dialog.get_by_label('类目', exact=True).select_option(category)
                    dialog.get_by_label('收款账户' if kind == 'income' else '付款账户', exact=True).select_option('wechat')
                    dialog.get_by_role('button', name='保存并备份').click()
                    expect(dialog).not_to_be_visible()
                    expect(page.get_by_role('heading', name='店铺总览', exact=True)).to_be_visible()

                for amount, category, purpose in [('16.80', 'ingredients', '猪肚鸡调料'), ('9.26', 'ingredients', '买菜'),
                                                  ('6.90', 'leisure', '网吧团购券'), ('2.00', 'transport', '第一次公交'),
                                                  ('2.00', 'transport', '第二次公交')]:
                    add(amount, category, purpose)
                expect(page.locator('.stat.featured .stat-value')).to_contain_text('36.96')
                print('PASS browser: five entries, classified total 36.96', flush=True)
                add('100.00', 'sales', '门店收款', kind='income')

                page.locator('[data-page=inventory]').click()
                page.get_by_role('button', name='新建商品 / 原料').click()
                dialog = page.get_by_role('dialog')
                dialog.get_by_label('名称', exact=True).fill('测试调料')
                dialog.get_by_label('单位', exact=True).fill('袋')
                dialog.get_by_label('补货线', exact=True).fill('2')
                dialog.get_by_role('button', name='创建并备份').click()
                expect(dialog).not_to_be_visible()
                product = page.locator('section.panel').first.locator('tbody tr').filter(has_text='测试调料')
                product.get_by_role('button', name='入库', exact=True).click()
                dialog = page.get_by_role('dialog')
                dialog.get_by_label('本次数量（袋）', exact=True).fill('10')
                dialog.get_by_label('同时新增一笔采购支出', exact=True).check()
                dialog.get_by_label('总金额（元）', exact=True).fill('20.00')
                dialog.get_by_role('button', name='确认入库并备份').click()
                expect(dialog).not_to_be_visible()
                product.get_by_role('button', name='出库', exact=True).click()
                dialog = page.get_by_role('dialog')
                dialog.get_by_label('本次数量（袋）', exact=True).fill('2')
                dialog.get_by_role('button', name='确认出库并备份').click()
                expect(dialog).not_to_be_visible()
                expect(product).to_contain_text('8 袋')
                print('PASS browser: stock in/out and linked purchase expense', flush=True)

                page.locator('[data-page=receipts]').click()
                page.locator('#image-input').set_input_files(str(fixture))
                expect(page.get_by_role('heading', name='核对截图账单', exact=True)).to_be_visible()
                form = page.locator('form[data-draft]').first
                expect(form).to_be_visible(timeout=20000)
                form.get_by_label('金额（元）', exact=True).fill('12.30')
                form.get_by_label('交易时间', exact=True).fill('2026-10-18T12:00')
                form.get_by_label('商品或用途', exact=True).fill('测试食材采购')
                form.get_by_label('类目', exact=True).select_option('ingredients')
                form.get_by_label('付款账户', exact=True).select_option('cash')
                form.get_by_role('button', name='确认本笔并入账').click()
                expect(page.get_by_text('本笔已经入账并备份', exact=False)).to_be_visible()
                print('PASS browser: image upload, review and posting with original backup', flush=True)

                page.locator('[data-page=transactions]').click()
                page.locator('tr[data-tx]').filter(has_text='猪肚鸡调料').click()
                page.get_by_role('button', name='登记退款', exact=True).click()
                dialog = page.get_by_role('dialog')
                expect(dialog.get_by_role('heading', name='记一笔', exact=True)).to_be_visible()
                dialog.get_by_label('金额（元）', exact=True).fill('2.00')
                dialog.get_by_label('交易时间', exact=True).fill('2026-10-19T12:00')
                dialog.get_by_role('button', name='保存并备份').click()
                expect(dialog).not_to_be_visible()
                page.locator('[data-page=overview]').click()
                expect(page.locator('.stat.featured .stat-value')).to_contain_text('67.26')
                page.screenshot(path=str(output/'desktop.png'), full_page=True)
                print('PASS browser: linked refund reduces expense to 67.26; income stays 100.00', flush=True)

                page.locator('[data-page=settings]').click()
                with page.expect_download() as event:
                    page.get_by_role('button', name='下载完整备份').click()
                bundle = output/'browser-backup.zip'
                event.value.save_as(str(bundle))
                with zipfile.ZipFile(bundle) as archive:
                    originals = [n for n in archive.namelist() if n.startswith('receipts/')]
                    assert len(originals) == 1
                    assert archive.read(originals[0]) == fixture.read_bytes()
                print('PASS browser: downloaded backup contains exact original image', flush=True)

                mobile = browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
                phone = mobile.new_page()
                phone.on('pageerror', lambda error: errors.append(str(error)))
                phone.goto(url)
                phone.get_by_label('六位连接码').fill('123456')
                phone.get_by_role('button', name='进入账本').click()
                expect(phone.locator('.stat.featured .stat-value')).to_contain_text('67.26')
                assert phone.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Mobile page overflows'
                phone.screenshot(path=str(output/'mobile.png'), full_page=True)
                phone.locator('[data-page=inventory]').click()
                expect(phone.get_by_role('heading', name='库存管理', exact=True)).to_be_visible()
                assert phone.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Mobile inventory overflows'
                print('PASS browser: separate phone session accesses same ledger and inventory; responsive width OK', flush=True)
                assert not errors, errors
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
            app.close()


if __name__ == '__main__':
    main()
