"""Restaurant sales. Prices, recipe consumption and payment are committed together."""
from collections import defaultdict
import json
import sqlite3

SCHEMA = '''
CREATE TABLE IF NOT EXISTS menu_items(
 id TEXT PRIMARY KEY,name TEXT NOT NULL,barcode TEXT UNIQUE NOT NULL,
 price_cents INTEGER NOT NULL CHECK(price_cents>0),stock_mode TEXT NOT NULL,
 active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS recipes(
 menu_id TEXT NOT NULL REFERENCES menu_items(id),product_id TEXT NOT NULL REFERENCES products(id),
 quantity_milli INTEGER NOT NULL CHECK(quantity_milli>0),PRIMARY KEY(menu_id,product_id));
CREATE TABLE IF NOT EXISTS orders(
 id TEXT PRIMARY KEY,number TEXT UNIQUE NOT NULL,occurred_at TEXT NOT NULL,
 total_cents INTEGER NOT NULL CHECK(total_cents>0),account_id TEXT NOT NULL REFERENCES accounts(id),
 transaction_id TEXT UNIQUE NOT NULL REFERENCES transactions(id),
 payment_method TEXT NOT NULL,payment_reference TEXT NOT NULL,note TEXT NOT NULL,
 status TEXT NOT NULL,created_at TEXT NOT NULL,idempotency_key TEXT UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS order_lines(
 id TEXT PRIMARY KEY,order_id TEXT NOT NULL REFERENCES orders(id),menu_id TEXT NOT NULL REFERENCES menu_items(id),
 name TEXT NOT NULL,barcode TEXT NOT NULL,price_cents INTEGER NOT NULL,quantity INTEGER NOT NULL,
 recipe_snapshot TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS order_refunds(
 id TEXT PRIMARY KEY,order_id TEXT UNIQUE NOT NULL REFERENCES orders(id),
 transaction_id TEXT UNIQUE NOT NULL REFERENCES transactions(id),reason TEXT NOT NULL,
 occurred_at TEXT NOT NULL,idempotency_key TEXT UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS shop_settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
'''


class Restaurant:
    def menu(self):
        with self.connect() as db:
            result = []
            for row in db.execute('SELECT * FROM menu_items ORDER BY name'):
                item = dict(row)
                item['recipe'] = [dict(r) for r in db.execute('''SELECT r.*,p.name,p.unit FROM recipes r
                    JOIN products p ON p.id=r.product_id WHERE r.menu_id=? ORDER BY p.name''', (row['id'],))]
                result.append(item)
            return result

    def save_menu(self, value, key=None):
        from .store import Problem, identifier, now, scaled, text
        name, barcode = text(value.get('name'), 100), text(value.get('barcode'), 100)
        mode = value.get('stock_mode')
        if not name or not barcode or mode not in ['recipe', 'none']:
            raise Problem('请填写菜品名称、唯一条码/编码，并选择原料扣减方式')
        price = scaled(value.get('price'))
        recipe = value.get('recipe', [])
        if not isinstance(recipe, list) or len(recipe) > 100:
            raise Problem('配方格式错误或超过100种原料')
        if mode == 'recipe' and not recipe:
            raise Problem('按配方扣库的菜品必须至少有一种原料')
        if mode == 'none' and recipe:
            raise Problem('手工领料模式不能同时设置自动扣库配方')
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if key:
                self._row(db, 'menu_items', key)
            key = key or identifier()
            seen, lines = set(), []
            for line in recipe:
                if not isinstance(line, dict):
                    raise Problem('配方条目无效')
                product = self._row(db, 'products', line.get('product_id'))
                if product['id'] in seen:
                    raise Problem('同一种原料请合并为一行')
                seen.add(product['id'])
                qty = scaled(line.get('quantity'), 1000)
                lines.append((key, product['id'], qty))
            active = 0 if value.get('active') is False else 1
            try:
                db.execute('''INSERT INTO menu_items VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,barcode=excluded.barcode,price_cents=excluded.price_cents,
                    stock_mode=excluded.stock_mode,active=excluded.active''',
                    (key, name, barcode, price, mode, active, now()))
            except sqlite3.IntegrityError as exc:
                raise Problem('菜品编码已被使用') from exc
            db.execute('DELETE FROM recipes WHERE menu_id=?', (key,))
            db.executemany('INSERT INTO recipes VALUES(?,?,?)', lines)
            self._event(db, 'menu', key, 'saved', {'item': dict(self._row(db, 'menu_items', key)),
                                                  'recipe': [dict(r) for r in db.execute('SELECT * FROM recipes WHERE menu_id=?', (key,))]})
        return self._finish({'id': key})

    def checkout(self, value):
        from .store import Problem, identifier, now, scaled, text, when, money
        idem = text(value.get('idempotency_key'), 100)
        if not idem:
            raise Problem('缺少收银请求编号')
        with self.lock:
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                old = db.execute('SELECT id FROM orders WHERE idempotency_key=?', (idem,)).fetchone()
                if old:
                    return self._finish({'id': old['id'], 'already_saved': True})
                if value.get('payment_confirmed') is not True:
                    raise Problem('请先核实已经实际收到款项，再确认销售')
                payment = value.get('payment_method')
                if payment not in ['cash', 'wechat', 'alipay', 'other']:
                    raise Problem('请选择实际收款方式')
                account = self._row(db, 'accounts', value.get('account_id'))['id']
                date = when(value.get('occurred_at'))
                items = value.get('items')
                if not isinstance(items, list) or not 1 <= len(items) <= 100:
                    raise Problem('购物车需要1至100行菜品')
                prepared, usage, total, seen = [], defaultdict(int), 0, set()
                for line in items:
                    if not isinstance(line, dict):
                        raise Problem('购物车格式错误')
                    item = self._row(db, 'menu_items', line.get('menu_id'))
                    if not item['active'] or item['id'] in seen:
                        raise Problem('菜品已停用或在购物车中重复，请刷新购物车')
                    seen.add(item['id'])
                    qty = scaled(line.get('quantity'), 1)
                    if qty > 999:
                        raise Problem('每行最多999份')
                    if line.get('expected_price_cents') != item['price_cents']:
                        raise Problem('菜品价格已变动，请刷新后重新核对收款金额')
                    recipe = [dict(r) for r in db.execute('''SELECT r.product_id,r.quantity_milli,p.name,p.unit
                        FROM recipes r JOIN products p ON p.id=r.product_id WHERE menu_id=?''', (item['id'],))]
                    if item['stock_mode'] == 'recipe' and not recipe:
                        raise Problem('菜品配方缺失，请先补全')
                    for raw in recipe:
                        usage[raw['product_id']] += raw['quantity_milli'] * qty
                    prepared.append((dict(item), qty, recipe))
                    total += item['price_cents'] * qty
                scaled(money(total))
                if value.get('expected_total_cents') != total:
                    raise Problem('购物车合计已变化，请重新核对实收金额')
                for product_id, needed in usage.items():
                    product = self._row(db, 'products', product_id)
                    if product['stock_milli'] < needed:
                        raise Problem('原料库存不足：' + product['name'] + '；本单尚未记账，请先盘点或入库')
                key, stamp = identifier(), now()
                sequence = db.execute('SELECT count(*) FROM orders').fetchone()[0] + 1
                number = 'S' + date[:10].replace('-', '') + '-' + str(sequence).zfill(6)
                reference = text(value.get('payment_reference'), 200)
                note = text(value.get('note'))
                tx = self._add_transaction(db, {'kind': 'income', 'amount': money(total), 'occurred_at': date,
                    'category_id': 'sales', 'account_id': account, 'purpose': '销售小票 ' + number,
                    'platform': {'cash': '现金收银', 'wechat': '微信收款', 'alipay': '支付宝收款', 'other': '其他收款'}[payment],
                    'reference': reference, 'idempotency_key': 'sale:' + key,
                    'source_note': '店员人工核实实收；软件未调用支付平台确认接口。'})
                data = dict(id=key, number=number, occurred_at=date, total_cents=total, account_id=account,
                    transaction_id=tx['id'], payment_method=payment, payment_reference=reference,
                    note=note, status='paid', created_at=stamp, idempotency_key=idem)
                db.execute('INSERT INTO orders VALUES(' + ','.join('?' for _ in data) + ')', tuple(data.values()))
                for item, qty, recipe in prepared:
                    db.execute('INSERT INTO order_lines VALUES(?,?,?,?,?,?,?,?)', (identifier(), key, item['id'],
                        item['name'], item['barcode'], item['price_cents'], qty, json.dumps(recipe, ensure_ascii=False)))
                for product_id, needed in usage.items():
                    move = dict(id=identifier(), product_id=product_id, kind='out', delta_milli=-needed,
                        occurred_at=date, note='销售配方领料 ' + number, transaction_id=tx['id'], created_at=stamp,
                        idempotency_key='sale:' + key + ':' + product_id)
                    db.execute('INSERT INTO movements VALUES(' + ','.join('?' for _ in move) + ')', tuple(move.values()))
                    db.execute('UPDATE products SET stock_milli=stock_milli-? WHERE id=?', (needed, product_id))
                    self._event(db, 'movement', move['id'], 'created', move)
                lines = [dict(r) for r in db.execute('SELECT * FROM order_lines WHERE order_id=?', (key,))]
                self._event(db, 'order', key, 'paid_manual_confirmation', {'order': data, 'lines': lines})
            return self._finish({'id': key, 'already_saved': False})

    def orders(self, month=None):
        from .store import Problem
        import re
        if month and not re.fullmatch(r'\d{4}-\d{2}', month):
            raise Problem('月份格式错误')
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT o.*,a.name AS account_name FROM orders o JOIN accounts a ON a.id=o.account_id ' +
                ('WHERE substr(o.occurred_at,1,7)=? ' if month else '') + 'ORDER BY o.created_at DESC,o.rowid DESC', (month,) if month else ())]

    def order(self, key):
        with self.connect() as db:
            item = dict(self._row(db, 'orders', key))
            item['account_name'] = self._row(db, 'accounts', item['account_id'])['name']
            item['lines'] = [dict(r) for r in db.execute('SELECT * FROM order_lines WHERE order_id=? ORDER BY rowid', (key,))]
            item['refund'] = next((dict(r) for r in db.execute('SELECT * FROM order_refunds WHERE order_id=?', (key,))), None)
            item['shop'] = self.shop()
            return item

    def refund_order(self, key, value):
        from .store import Problem, identifier, text, when, money
        reason, idem = text(value.get('reason')), text(value.get('idempotency_key'), 100)
        if not reason or not idem or value.get('payment_confirmed') is not True:
            raise Problem('请填写退款原因，并确认已经实际退还款项')
        with self.lock:
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                old = db.execute('SELECT id,order_id FROM order_refunds WHERE idempotency_key=?', (idem,)).fetchone()
                if old:
                    if old['order_id'] != key:
                        raise Problem('请求编号已用于另一笔退款')
                    return self._finish({'id': old['id'], 'already_saved': True})
                order = self._row(db, 'orders', key)
                if order['status'] != 'paid':
                    raise Problem('该订单已经退单，不可重复退款')
                date = when(value.get('occurred_at'))
                tx = self._add_transaction(db, {'kind': 'sale_refund', 'refund_of': order['transaction_id'],
                    'amount': money(order['total_cents']), 'occurred_at': date, 'account_id': value.get('account_id'),
                    'purpose': '销售退单 ' + order['number'], 'source_note': '人工确认退款；食品原料不自动回库。',
                    'idempotency_key': 'sale-refund:' + key}, sales_refund=True)
                data = dict(id=identifier(), order_id=key, transaction_id=tx['id'], reason=reason,
                            occurred_at=date, idempotency_key=idem)
                db.execute('INSERT INTO order_refunds VALUES(?,?,?,?,?,?)', tuple(data.values()))
                db.execute("UPDATE orders SET status='refunded' WHERE id=?", (key,))
                self._event(db, 'order', key, 'refunded_without_restock', data)
            return self._finish({'id': data['id']})

    def shop(self):
        with self.connect() as db:
            settings = {r['key']: r['value'] for r in db.execute('SELECT * FROM shop_settings')}
        return {'name': settings.get('name', '我的餐饮店'), 'address': settings.get('address', ''),
                'phone': settings.get('phone', ''), 'footer': settings.get('footer', '谢谢惠顾，请妥善保管小票')}

    def save_shop(self, value):
        from .store import text, Problem
        data = {k: text(value.get(k), 150) for k in ['name', 'address', 'phone', 'footer']}
        if not data['name']:
            raise Problem('请填写店铺名称')
        with self.lock, self.connect() as db:
            for k, v in data.items():
                db.execute('INSERT INTO shop_settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (k, v))
            self._event(db, 'shop', 'settings', 'saved', data)
        return self._finish({'ok': True})
