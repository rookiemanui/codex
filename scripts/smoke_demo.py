"""Validate the standalone public demo without the private ledger or any backend."""
from pathlib import Path
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import threading
from playwright.sync_api import sync_playwright, expect


def main():
    root = Path(__file__).resolve().parents[1]
    output = root / 'output/playwright/public-demo'
    output.mkdir(parents=True, exist_ok=True)
    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler, directory=str(root / 'docs')))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_address[1]}/index.html'
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox'])
        context = browser.new_context(viewport={'width': 1440, 'height': 1100})
        page = context.new_page()
        errors, network = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda request: network.append(request.url) if request.url.startswith(('http:', 'https:')) and request.resource_type != 'document' else None)
        page.goto(url)
        expect(page.get_by_role('heading', name='店铺总览', exact=True)).to_be_visible()
        expect(page.locator('.metric')).to_have_text(['¥68.40', '¥120.00', '¥51.60', '1 项'])
        page.get_by_role('button', name='＋ 试记一笔').click()
        dialog = page.get_by_role('dialog')
        dialog.get_by_label('金额（元）').fill('1.005')
        dialog.get_by_label('用途').fill('演示测试采购')
        dialog.get_by_role('button', name='添加演示账目').click()
        expect(dialog.locator('.form-error')).to_contain_text('最多两位小数')
        dialog.get_by_label('金额（元）').fill('20.10')
        dialog.get_by_role('button', name='添加演示账目').click()
        expect(page.locator('.metric').first).to_have_text('¥88.50')
        page.get_by_role('button', name='重置演示').click()
        page.locator('[data-page=pos]').click()
        page.locator('[data-dish=chicken]').click()
        page.locator('[data-dish=rice]').click()
        page.locator('[data-dish=rice]').click()
        expect(page.locator('.cart-total')).to_contain_text('22.00')
        page.get_by_role('button', name='确认模拟销售').click()
        expect(page.locator('.ticket-total')).to_have_text('合计 ¥22.00')
        page.evaluate('window.print = () => { window.printInvoked = true; }')
        page.get_by_role('button', name='打印示例小票 / 保存PDF').click()
        assert page.evaluate('window.printInvoked')
        page.emulate_media(media='print')
        assert page.locator('.shell').evaluate('(e)=>getComputedStyle(e).display') == 'none'
        page.pdf(path=str(output/'demo-ticket.pdf'), width='80mm', height='210mm')
        page.emulate_media(media='screen')
        page.get_by_role('button', name='关闭', exact=True).click()
        page.locator('[data-page=inventory]').click()
        expect(page.get_by_role('row').filter(has_text='鸡肉')).to_contain_text('4 千克')
        expect(page.get_by_role('row').filter(has_text='大米')).to_contain_text('1.2 千克')
        page.locator('[data-page=overview]').click()
        expect(page.locator('.metric').nth(1)).to_have_text('¥142.00')
        expect(page.locator('.metric').nth(2)).to_have_text('¥73.60')
        page.get_by_role('button', name='重置演示').click()
        page.locator('[data-page=inventory]').click()
        page.locator('[data-adjust=rice]').click()
        page.get_by_label('盘点后数量').fill('0.05')
        page.get_by_role('button', name='保存演示数量').click()
        page.locator('[data-page=pos]').click()
        page.locator('[data-dish=rice]').click()
        page.get_by_role('button', name='确认模拟销售').click()
        expect(page.locator('#toast')).to_contain_text('库存不足')
        expect(page.get_by_role('dialog')).not_to_be_visible()
        page.locator('[data-page=overview]').click()
        expect(page.locator('.metric').nth(1)).to_have_text('¥120.00')
        page.get_by_role('button', name='重置演示').click()
        page.screenshot(path=str(output/'desktop.png'), full_page=True)
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(output/'mobile.png'), full_page=True)
        page.locator('[data-page=ledger]').click()
        page.get_by_label('搜索演示账目').fill('调料')
        expect(page.locator('tbody tr')).to_have_count(1)
        page.reload()
        expect(page.locator('.metric')).to_have_text(['¥68.40', '¥120.00', '¥51.60', '1 项'])
        assert not errors, errors
        assert not network, network
        browser.close()
    server.shutdown()
    server.server_close()
    thread.join()
    print('PASS: single-file demo served by HTTP, synthetic totals, precision validation, sale/stock consistency, insufficient-stock rollback, print/PDF, search, reset, mobile layout; no API or external resource requests')


if __name__ == '__main__':
    main()
