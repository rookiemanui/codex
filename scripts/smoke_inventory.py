"""Browser inventory and manual-refund acceptance checks using a synthetic ledger."""
import argparse
from pathlib import Path
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from localledger.server import Application, make_server
from playwright.sync_api import expect, sync_playwright
from scripts.browser_test_support import launch_browser


NAME = '库存验收鸡肉（模拟）'


def login(page, url):
    page.goto(url)
    page.get_by_label('六位连接码').fill('123456')
    page.get_by_role('button', name='进入账本', exact=True).click()
    page.locator('[data-page=inventory]').click()
    expect(page.get_by_role('heading', name='当前库存', exact=True)).to_be_visible()


def inventory_row(page):
    return page.locator('#content section.panel').first.locator('tbody tr').filter(has_text=NAME)


def movement_form(page, action, quantity, note):
    inventory_row(page).get_by_role('button', name=action, exact=True).click()
    form = page.locator('#movement-form')
    form.locator('[name=quantity]').fill(quantity)
    form.get_by_label('说明', exact=True).fill(note)
    return form


def submit_movement(page):
    page.locator('#movement-form button[type=submit]').click()
    expect(page.get_by_role('dialog')).not_to_be_visible()


def manual_refund(page, app, baseline_income, output):
    page.locator('[data-page=transactions]').click()
    page.locator('#content [data-new-entry]').click()
    page.locator('.kind-tabs [data-kind=income]').click()
    form = page.locator('#entry-form')
    form.get_by_label('金额（元）', exact=True).fill('20.00')
    form.get_by_label('商品或用途', exact=True).fill('模拟手录营业收入')
    form.get_by_label('类目', exact=True).select_option('sales')
    form.get_by_label('收款账户', exact=True).select_option('cash')
    form.get_by_role('button', name='保存并备份', exact=True).click()
    expect(page.get_by_role('dialog')).not_to_be_visible()
    income = next(row for row in app.store.transactions() if row['purpose'] == '模拟手录营业收入')
    page.locator('[data-tx="' + income['id'] + '"]').click()
    page.get_by_role('button', name='登记退款', exact=True).click()
    form = page.locator('#entry-form')
    expect(form.get_by_label('关联原收入', exact=True)).to_have_value(income['id'])
    form.get_by_label('金额（元）', exact=True).fill('5.00')
    form.get_by_role('button', name='保存并备份', exact=True).click()
    expect(page.get_by_role('dialog')).not_to_be_visible()
    summary = app.store.summary(None)
    assert summary['income_cents'] == baseline_income + 1500, summary
    refunds = [row for row in app.store.transactions() if row['refund_of'] == income['id']]
    assert len(refunds) == 1 and refunds[0]['amount_cents'] == 500
    assert refunds[0]['kind'] == 'sale_refund'
    page.locator('[data-tx="' + income['id'] + '"]').click()
    expect(page.get_by_role('dialog')).to_contain_text('5.00')
    page.screenshot(path=str(output / 'manual-income-refund-synthetic.png'), full_page=True)
    page.get_by_role('button', name='关闭', exact=True).click()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory-only', action='store_true', help='Run inventory checks without manual-income refunds')
    args = parser.parse_args()
    output = Path('output/playwright/inventory')
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ledger-inventory-synthetic-') as directory:
        app = Application(Path(directory) / 'data', code='123456', test_mode=True)
        baseline = app.store.summary(None)
        server = make_server(app, port=0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        errors = []
        try:
            with sync_playwright() as p:
                browser = launch_browser(p)
                try:
                    desktop = browser.new_context(viewport={'width': 1440, 'height': 1000})
                    page = desktop.new_page()
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.on('dialog', lambda dialog: dialog.accept())
                    url = f'http://127.0.0.1:{server.server_address[1]}'
                    login(page, url)
                    page.get_by_role('button', name='新建商品 / 原料', exact=True).click()
                    form = page.locator('#product-form')
                    form.get_by_label('名称', exact=True).fill(NAME)
                    form.get_by_label('单位', exact=True).fill('千克')
                    form.get_by_label('补货线', exact=True).fill('1')
                    form.get_by_role('button', name='创建并备份', exact=True).click()
                    expect(inventory_row(page)).to_be_visible()
                    raw = next(row['id'] for row in app.store.products() if row['name'] == NAME)

                    def stock():
                        return next(row['stock_milli'] for row in app.store.products() if row['id'] == raw)

                    # A real form submits both the purchase and stock in one transaction.
                    form = movement_form(page, '入库', '5', '模拟采购入库')
                    form.get_by_label('同时新增一笔采购支出', exact=True).check()
                    form.get_by_label('总金额（元）', exact=True).fill('50.00')
                    form.get_by_label('账户', exact=True).select_option('cash')
                    form.get_by_label('收支类目', exact=True).select_option('ingredients')
                    submit_movement(page)
                    assert stock() == 5000
                    assert app.store.summary(None)['expense_cents'] == baseline['expense_cents'] + 5000
                    purchase_transactions = len(app.store.transactions())

                    # Changing the action clears a previously selected financial link.
                    form = movement_form(page, '出库', '0.5', '模拟损耗')
                    form.get_by_label('同时新增一笔销售收入', exact=True).check()
                    form.get_by_label('总金额（元）', exact=True).fill('12.00')
                    form.get_by_label('库存动作', exact=True).select_option('loss')
                    expect(form.locator('#finance-section')).to_be_hidden()
                    expect(form.locator('#link-finance')).not_to_be_checked()
                    assert not form.locator('[name=linked_amount]').evaluate('(element) => element.required')
                    submit_movement(page)
                    assert stock() == 4500
                    assert len(app.store.transactions()) == purchase_transactions
                    assert app.store.summary(None)['income_cents'] == baseline['income_cents']

                    # Drop only the response; route.fetch has already committed on the server.
                    committed = {}

                    def lose_response(route):
                        committed['request'] = route.request.post_data_json
                        response = route.fetch()
                        assert response.status == 200, response.text()
                        committed['result'] = response.json()
                        route.abort('failed')

                    form = movement_form(page, '入库', '1', '模拟断响采购')
                    form.get_by_label('同时新增一笔采购支出', exact=True).check()
                    form.get_by_label('总金额（元）', exact=True).fill('10.00')
                    page.route('**/api/movements', lose_response, times=1)
                    form.get_by_role('button', name='确认入库并备份', exact=True).click()
                    expect(form.locator('.form-error')).to_contain_text('结果待核实')
                    assert stock() == 5500
                    count_after_commit = len(app.store.movements())
                    transactions_after_commit = len(app.store.transactions())
                    page.route('**/api/movements', lambda route: route.fulfill(
                        status=403, content_type='application/json', body='{"error":"session expired"}'), times=1)
                    form.get_by_role('button', name='核实原请求', exact=True).click()
                    expect(form.locator('.form-error')).to_contain_text('session expired')
                    assert page.evaluate('(key) => localStorage.getItem(key)',
                                         'ledger-pending-inventory:' + app.store.ledger_id) is not None
                    page.reload()
                    page.locator('[data-page=inventory]').click()
                    page.get_by_role('button', name='核实上次操作', exact=True).click()
                    with page.expect_request(lambda request: request.method == 'POST' and request.url.endswith('/api/movements')) as retry:
                        page.get_by_role('button', name='核实原请求', exact=True).click()
                    expect(page.get_by_role('dialog')).not_to_be_visible()
                    assert retry.value.post_data_json == committed['request']
                    assert len(app.store.movements()) == count_after_commit
                    assert len(app.store.transactions()) == transactions_after_commit
                    assert stock() == 5500
                    assert page.evaluate('(key) => localStorage.getItem(key)', 'ledger-pending-inventory:' + app.store.ledger_id) is None
                    expect(inventory_row(page)).to_contain_text('5.5 千克')
                    page.screenshot(path=str(output / 'recovered-purchase-synthetic.png'), full_page=True)
                    print('PASS: purchase linkage, loss without finance, committed/lost-response retry survives refresh without duplicates')

                    # Two isolated cookie sessions share the same authoritative database.
                    second = browser.new_context(viewport={'width': 1280, 'height': 900})
                    other = second.new_page()
                    other.on('pageerror', lambda error: errors.append(str(error)))
                    login(other, url)
                    expect(inventory_row(other)).to_contain_text('5.5 千克')
                    count_form = movement_form(page, '盘点', '6', '模拟陈旧盘点')
                    movement_form(other, '入库', '1', '模拟另一设备入库')
                    submit_movement(other)
                    assert stock() == 6500
                    count_form.get_by_role('button', name='确认盘点并备份', exact=True).click()
                    expect(count_form.locator('.form-error')).to_contain_text('另一台设备')
                    assert stock() == 6500
                    assert not any(row['note'] == '模拟陈旧盘点' for row in app.store.movements())
                    page.screenshot(path=str(output / 'stale-count-rejected-synthetic.png'), full_page=True)
                    page.get_by_role('button', name='关闭', exact=True).click()
                    page.locator('[data-page=overview]').click()
                    page.locator('[data-page=inventory]').click()
                    expect(inventory_row(page)).to_contain_text('6.5 千克')

                    # Undo the extra purchase and its associated expense, retaining its history.
                    movement = page.locator('#content section.panel').last.locator('tbody tr').filter(has_text='模拟断响采购')
                    movement.get_by_role('button', name='撤销录错', exact=True).click()
                    form = page.locator('#reverse-movement-form')
                    form.get_by_label('撤销原因', exact=True).fill('模拟采购重复登记，验证纠正流程')
                    form.get_by_role('button', name='确认撤销并备份', exact=True).click()
                    expect(page.get_by_role('dialog')).not_to_be_visible()
                    assert stock() == 5500
                    assert app.store.summary(None)['expense_cents'] == baseline['expense_cents'] + 5000
                    original = next(row for row in app.store.movements() if row['id'] == committed['result']['id'])
                    assert original['reversed_by']
                    transaction = next(row for row in app.store.transactions() if row['id'] == original['transaction_id'])
                    assert transaction['voided_at']
                    expect(movement).to_contain_text('已撤销')
                    page.screenshot(path=str(output / 'reversal-desktop-synthetic.png'), full_page=True)
                    other.reload()
                    other.locator('[data-page=inventory]').click()
                    expect(inventory_row(other)).to_contain_text('5.5 千克')
                    other.set_viewport_size({'width': 390, 'height': 844})
                    assert other.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    other.screenshot(path=str(output / 'shared-inventory-mobile-synthetic.png'), full_page=True)
                    print('PASS: two independent sessions share stock; stale count refused; reversal fixes stock and expense with retained history; mobile layout')
                    if not args.inventory_only:
                        manual_refund(page, app, baseline['income_cents'], output)
                        print('PASS: manual income partial refund reduces income and retains its linked original')
                    assert not errors, errors
                except Exception:
                    if 'page' in locals() and not page.is_closed():
                        page.screenshot(path=str(output / 'failure-synthetic.png'), full_page=True)
                    raise
                finally:
                    browser.close()
        finally:
            server.shutdown()
            server.server_close()
            app.close()
            worker.join()


if __name__ == '__main__':
    main()
