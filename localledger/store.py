from __future__ import annotations

import csv
from contextlib import closing, contextmanager
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import threading
from uuid import uuid4
import zipfile


from .restaurant import Restaurant, SCHEMA as RESTAURANT_SCHEMA


class Problem(ValueError):
    pass


def now():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def identifier():
    return uuid4().hex


def scaled(value, scale=100, allow_zero=False, signed=False):
    try:
        number = Decimal(str(value))
        result = number * scale
        if not number.is_finite() or result != result.to_integral_value():
            raise Problem('金额最多两位小数；数量最多三位小数')
        integer = int(result)
        if abs(integer) > 10**12:
            raise Problem('数值过大')
        if not signed and integer < 0:
            raise Problem('请输入正数，收支方向由交易类型决定')
        if not allow_zero and integer == 0:
            raise Problem('请输入大于零的数值')
        return integer
    except (InvalidOperation, TypeError, ValueError) as exc:
        if isinstance(exc, Problem):
            raise
        raise Problem('请输入有效的金额或数量') from exc


def money(cents):
    return f'{Decimal(cents) / 100:.2f}'


def quantity(milli):
    result = format(Decimal(milli) / 1000, 'f')
    return result.rstrip('0').rstrip('.') if '.' in result else result


def when(value):
    try:
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (ValueError, TypeError) as exc:
        raise Problem('请确认交易日期和时间') from exc
    return result.isoformat(sep=' ', timespec='seconds')


def text(value, maximum=500):
    value = str(value or '').strip()
    if len(value) > maximum:
        raise Problem('文字内容过长')
    return value


DEFAULT_CATEGORIES = [
    ('sales', '销售收款', 'income'), ('other_income', '其他收入', 'income'),
    ('purchase', '进货采购', 'expense'), ('ingredients', '餐饮食材', 'expense'),
    ('rent', '房租水电', 'expense'), ('salary', '员工薪资', 'expense'),
    ('transport', '交通出行', 'expense'), ('supplies', '日常杂费', 'expense'),
    ('leisure', '休闲娱乐', 'expense'),
]


class Store(Restaurant):
    def __init__(self, directory):
        self.root = Path(directory).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / 'ledger.sqlite3'
        self.lock = threading.RLock()
        self._last_backed_sequence = 0
        for part in ['receipts', 'backups/receipts', 'backups/records', 'backups/database']:
            (self.root / part).mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                INSERT OR IGNORE INTO metadata VALUES('schema_version','1');
                CREATE TABLE IF NOT EXISTS accounts(id TEXT PRIMARY KEY,name TEXT UNIQUE NOT NULL);
                CREATE TABLE IF NOT EXISTS categories(id TEXT PRIMARY KEY,name TEXT NOT NULL,kind TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS receipts(
                    id TEXT PRIMARY KEY, sha256 TEXT UNIQUE NOT NULL, filename TEXT NOT NULL,
                    extension TEXT NOT NULL, created_at TEXT NOT NULL, status TEXT NOT NULL,
                    ocr_text TEXT NOT NULL DEFAULT '',error TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS drafts(
                    id TEXT PRIMARY KEY,receipt_id TEXT NOT NULL REFERENCES receipts(id),
                    data TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'review');
                CREATE TABLE IF NOT EXISTS transactions(
                    id TEXT PRIMARY KEY,kind TEXT NOT NULL,amount_cents INTEGER NOT NULL CHECK(amount_cents>0),
                    occurred_at TEXT NOT NULL,category_id TEXT REFERENCES categories(id),
                    account_id TEXT NOT NULL REFERENCES accounts(id),to_account_id TEXT REFERENCES accounts(id),
                    purpose TEXT NOT NULL DEFAULT '',merchant TEXT NOT NULL DEFAULT '',
                    reference TEXT NOT NULL DEFAULT '',platform TEXT NOT NULL DEFAULT '',
                    receipt_id TEXT REFERENCES receipts(id),draft_id TEXT UNIQUE REFERENCES drafts(id),
                    refund_of TEXT REFERENCES transactions(id),created_at TEXT NOT NULL,
                    voided_at TEXT,idempotency_key TEXT UNIQUE NOT NULL,
                    source_note TEXT NOT NULL DEFAULT '',time_precision TEXT NOT NULL DEFAULT 'unknown');
                CREATE UNIQUE INDEX IF NOT EXISTS idx_reference ON transactions(platform,reference,kind)
                    WHERE reference!='' AND voided_at IS NULL;
                CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions(occurred_at);
                CREATE INDEX IF NOT EXISTS idx_tx_refund ON transactions(refund_of) WHERE voided_at IS NULL;
                CREATE TABLE IF NOT EXISTS reconciliations(
                    id TEXT PRIMARY KEY,day TEXT NOT NULL,scope TEXT NOT NULL,kind TEXT NOT NULL,
                    amount_cents INTEGER NOT NULL,note TEXT NOT NULL,version INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,UNIQUE(day,scope,kind));
                CREATE TABLE IF NOT EXISTS rules(id TEXT PRIMARY KEY,keyword TEXT NOT NULL UNIQUE,
                    category_id TEXT NOT NULL REFERENCES categories(id),field TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS products(id TEXT PRIMARY KEY,name TEXT NOT NULL,
                    sku TEXT UNIQUE NOT NULL,unit TEXT NOT NULL,stock_milli INTEGER NOT NULL DEFAULT 0,
                    minimum_milli INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS movements(id TEXT PRIMARY KEY,product_id TEXT NOT NULL REFERENCES products(id),
                    kind TEXT NOT NULL,delta_milli INTEGER NOT NULL,occurred_at TEXT NOT NULL,
                    note TEXT NOT NULL,transaction_id TEXT REFERENCES transactions(id),created_at TEXT NOT NULL,
                    idempotency_key TEXT UNIQUE NOT NULL);
                CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,entity TEXT NOT NULL,
                    entity_id TEXT NOT NULL,action TEXT NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL);
            ''')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(transactions)')}
            if 'source_note' not in columns:
                db.execute("ALTER TABLE transactions ADD COLUMN source_note TEXT NOT NULL DEFAULT ''")
            if 'time_precision' not in columns:
                db.execute("ALTER TABLE transactions ADD COLUMN time_precision TEXT NOT NULL DEFAULT 'unknown'")
            db.executescript(RESTAURANT_SCHEMA)
            db.execute("UPDATE metadata SET value='4' WHERE key='schema_version'")
            db.execute("INSERT OR IGNORE INTO metadata VALUES('ledger_id',?)", (identifier(),))
            self.ledger_id = db.execute("SELECT value FROM metadata WHERE key='ledger_id'").fetchone()[0]
            for key, label, kind in DEFAULT_CATEGORIES:
                db.execute('INSERT OR IGNORE INTO categories VALUES(?,?,?)', (key, label, kind))
            for key, name in [('cash', '现金'), ('wechat', '微信零钱'), ('alipay', '支付宝余额'), ('bank', '银行卡')]:
                db.execute('INSERT OR IGNORE INTO accounts VALUES(?,?)', (key, name))
        self.recover_backups()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def _event(self, db, entity, entity_id, action, payload):
        key = identifier()
        db.execute('INSERT INTO events VALUES(?,?,?,?,?,?)',
                   (key, entity, entity_id, action, json.dumps(payload, ensure_ascii=False), now()))

    def recover_backups(self, full=True):
        """Recover file backups from the transactional outbox after a crash."""
        with self.lock, self.connect() as db:
            rows = db.execute('SELECT rowid AS backup_sequence,* FROM events ' +
                              ('' if full else 'WHERE rowid>? ') + 'ORDER BY rowid',
                              () if full else (self._last_backed_sequence,)).fetchall()
            for row in rows:
                expected = {k: row[k] for k in row.keys() if k != 'backup_sequence'}
                destination = self.root / 'backups/records' / (row['id'] + '.json')
                try:
                    intact = json.loads(destination.read_text(encoding='utf-8')) == expected
                except (OSError, ValueError):
                    intact = False
                if not intact:
                    self._atomic_json(destination, expected)
                self._last_backed_sequence = max(self._last_backed_sequence, row['backup_sequence'])
            if full:
                for receipt in db.execute('SELECT sha256,extension FROM receipts'):
                    name = receipt['sha256'] + receipt['extension']
                    original = self.root / 'receipts' / name
                    backup = self.root / 'backups/receipts' / name
                    original_ok = original.is_file() and hashlib.sha256(original.read_bytes()).hexdigest() == receipt['sha256']
                    backup_ok = backup.is_file() and hashlib.sha256(backup.read_bytes()).hexdigest() == receipt['sha256']
                    if original_ok and not backup_ok:
                        shutil.copy2(original, backup)
                    elif backup_ok and not original_ok:
                        shutil.copy2(backup, original)
                    elif not original_ok:
                        raise Problem('原始凭证及其副本均缺失或损坏，请从其他完整备份恢复：' + name)

    @staticmethod
    def _atomic_json(path, value):
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(path)

    def _backup(self):
        self.recover_backups(full=False)
        day = datetime.now().strftime('%Y-%m-%d')
        path = self.root / 'backups/database' / (day + '.sqlite3')
        temporary = path.with_suffix('.tmp')
        with self.connect() as source, closing(sqlite3.connect(temporary)) as target:
            source.backup(target)
        temporary.replace(path)

    def _finish(self, result):
        with self.lock:
            try:
                self._backup()
                result['backup_ok'] = True
            except (OSError, sqlite3.Error):
                result['backup_ok'] = False
                result['backup_warning'] = '账目已保存，但独立备份未完成；请检查数据目录剩余空间后重试备份'
        return result

    def options(self):
        with self.connect() as db:
            return {name: [dict(x) for x in db.execute(f'SELECT * FROM {name} ORDER BY name')]
                    for name in ['accounts', 'categories']}

    def add_account(self, value):
        name = text(value.get('name'), 80)
        if not name:
            raise Problem('请输入账户名称')
        with self.lock, self.connect() as db:
            key = identifier()
            try:
                db.execute('INSERT INTO accounts VALUES(?,?)', (key, name))
            except sqlite3.IntegrityError as exc:
                raise Problem('账户名称已存在') from exc
            self._event(db, 'account', key, 'created', {'name': name})
        return self._finish({'id': key})

    def add_category(self, value):
        name, kind = text(value.get('name'), 80), value.get('kind')
        if not name or kind not in ['income', 'expense']:
            raise Problem('请填写类目名称和收支方向')
        with self.lock, self.connect() as db:
            key = identifier()
            db.execute('INSERT INTO categories VALUES(?,?,?)', (key, name, kind))
            self._event(db, 'category', key, 'created', {'name': name, 'kind': kind})
        return self._finish({'id': key})

    def _row(self, db, table, key):
        if table not in {'accounts', 'categories', 'transactions', 'drafts', 'receipts', 'products', 'menu_items', 'orders', 'rules'}:
            raise Problem('记录类型无效')
        row = db.execute(f'SELECT * FROM {table} WHERE id=?', (key,)).fetchone()
        if not row:
            raise Problem('记录不存在或已变更，请刷新页面')
        return row

    def _add_transaction(self, db, value, sales_refund=False):
        kind = value.get('kind')
        if kind == 'sale_refund' and not sales_refund:
            raise Problem('销售退款请在销售订单中办理退单')
        if kind not in ['expense', 'income', 'transfer', 'refund', 'sale_refund']:
            raise Problem('交易类型无效')
        idem = text(value.get('idempotency_key'), 100)
        if not idem:
            raise Problem('缺少请求编号，请重新打开记账窗口')
        existing = db.execute('SELECT id FROM transactions WHERE idempotency_key=?', (idem,)).fetchone()
        if existing:
            return {'id': existing['id'], 'already_saved': True}
        amount, date = scaled(value.get('amount')), when(value.get('occurred_at'))
        account = self._row(db, 'accounts', value.get('account_id'))['id']
        category, target, original = value.get('category_id'), None, None
        if kind in ['refund', 'sale_refund']:
            original = self._row(db, 'transactions', value.get('refund_of'))
            if original['kind'] != ('expense' if kind == 'refund' else 'income') or original['voided_at']:
                raise Problem('退款必须关联正确方向的有效原账目')
            refunded = db.execute("SELECT COALESCE(SUM(amount_cents),0) FROM transactions WHERE refund_of=? AND voided_at IS NULL", (original['id'],)).fetchone()[0]
            if amount > original['amount_cents'] - refunded:
                raise Problem('退款金额超过原账目的可退余额')
            category = original['category_id']
        elif kind == 'transfer':
            target = self._row(db, 'accounts', value.get('to_account_id'))['id']
            if target == account:
                raise Problem('转出与转入账户不能相同')
            category = None
        else:
            category_row = self._row(db, 'categories', category)
            if category_row['kind'] != kind:
                raise Problem('类目与收支方向不一致')
        receipt, draft_id = value.get('receipt_id') or None, value.get('draft_id') or None
        if draft_id:
            draft = self._row(db, 'drafts', draft_id)
            if draft['status'] == 'posted':
                raise Problem('该截图条目已经入账，请勿重复提交')
            receipt = draft['receipt_id']
        if receipt:
            self._row(db, 'receipts', receipt)
        key = identifier()
        raw_time = str(value.get('occurred_at', ''))
        precision = value.get('time_precision') or ('date' if len(raw_time) == 10 else 'minute' if re.fullmatch(r'.*[ T]\d{2}:\d{2}', raw_time) else 'second')
        if precision not in ['date', 'minute', 'second', 'unknown']:
            raise Problem('时间精度标记无效')
        data = {'id': key, 'kind': kind, 'amount_cents': amount, 'occurred_at': date,
                'category_id': category, 'account_id': account, 'to_account_id': target,
                'purpose': text(value.get('purpose')), 'merchant': text(value.get('merchant')),
                'reference': text(value.get('reference'), 200), 'platform': text(value.get('platform'), 100),
                'receipt_id': receipt, 'draft_id': draft_id, 'refund_of': original['id'] if original else None,
                'created_at': now(), 'voided_at': None, 'idempotency_key': idem,
                'source_note': text(value.get('source_note'), 3000), 'time_precision': precision}
        try:
            db.execute('INSERT INTO transactions(' + ','.join(data) + ') VALUES(' + ','.join('?' for _ in data) + ')', tuple(data.values()))
        except sqlite3.IntegrityError as exc:
            raise Problem('这笔交易编号或截图条目已经入账，请核对已有账目') from exc
        if draft_id:
            db.execute("UPDATE drafts SET status='posted' WHERE id=?", (draft_id,))
        if value.get('learn_keyword') and kind in ['expense', 'income']:
            self._learn(db, value['learn_keyword'], category, 'purpose')
        self._event(db, 'transaction', key, 'created', data)
        return {'id': key, 'already_saved': False}

    def add_transaction(self, value):
        with self.lock:
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                sales_refund = value.get('kind') == 'sale_refund'
                if sales_refund and db.execute('SELECT 1 FROM orders WHERE transaction_id=?', (value.get('refund_of'),)).fetchone():
                    raise Problem('收银订单的销售退款请在销售订单中办理退单')
                result = self._add_transaction(db, value, sales_refund=sales_refund)
            return self._finish(result)

    def annotate_source(self, key, source_note, precision):
        if precision not in ['date', 'minute', 'second', 'unknown']:
            raise Problem('时间精度标记无效')
        with self.lock, self.connect() as db:
            before = dict(self._row(db, 'transactions', key))
            if before['source_note'] == source_note and before['time_precision'] == precision:
                return {'ok': True, 'unchanged': True}
            db.execute('UPDATE transactions SET source_note=?,time_precision=? WHERE id=?', (source_note, precision, key))
            self._event(db, 'transaction', key, 'source_metadata_added', {'before': before, 'after': dict(self._row(db, 'transactions', key))})
        return self._finish({'ok': True})

    def _learn(self, db, keyword, category, field):
        keyword = text(keyword, 80)
        if len(keyword) < 2 or keyword in ['淘宝', '抖音', '微信', '支付宝', '京东', '美团', '订单', '商品', '消费']:
            raise Problem('请使用具体用途关键词，例如“猪肚鸡调料”，不要用综合平台名称')
        self._row(db, 'categories', category)
        db.execute('INSERT INTO rules VALUES(?,?,?,?) ON CONFLICT(keyword) DO UPDATE SET category_id=excluded.category_id,field=excluded.field',
                   (identifier(), keyword, category, field))
        self._event(db, 'rule', keyword, 'learned', {'keyword': keyword, 'category_id': category, 'field': field})

    def rules(self):
        with self.connect() as db:
            return [dict(x) for x in db.execute('SELECT r.*,c.name AS category_name FROM rules r JOIN categories c ON c.id=r.category_id ORDER BY length(keyword) DESC')]

    def delete_rule(self, key):
        with self.lock, self.connect() as db:
            row = dict(self._row(db, 'rules', key))
            db.execute('DELETE FROM rules WHERE id=?', (key,))
            self._event(db, 'rule', key, 'deleted', row)
        return self._finish({'ok': True})

    def reclassify(self, key, value):
        with self.lock, self.connect() as db:
            row = self._row(db, 'transactions', key)
            if row['voided_at'] or row['kind'] not in ['income', 'expense']:
                raise Problem('只有有效收入和支出可以修改类目')
            category = self._row(db, 'categories', value.get('category_id'))
            if category['kind'] != row['kind']:
                raise Problem('类目方向不一致')
            purpose = text(value.get('purpose', row['purpose']))
            affected = [dict(row)] + [dict(x) for x in db.execute('SELECT * FROM transactions WHERE refund_of=?', (key,))]
            db.execute('UPDATE transactions SET category_id=?,purpose=? WHERE id=?', (category['id'], purpose, key))
            db.execute('UPDATE transactions SET category_id=? WHERE refund_of=?', (category['id'], key))
            for previous in affected:
                updated = dict(self._row(db, 'transactions', previous['id']))
                self._event(db, 'transaction', previous['id'], 'reclassified', {'before': previous, 'after': updated})
            if value.get('learn_keyword'):
                self._learn(db, value['learn_keyword'], category['id'], 'purpose')
        return self._finish({'ok': True})

    def void(self, key):
        with self.lock, self.connect() as db:
            row = dict(self._row(db, 'transactions', key))
            if row['voided_at']:
                raise Problem('该笔已经作废')
            if db.execute('SELECT 1 FROM orders WHERE transaction_id=? UNION ALL SELECT 1 FROM order_refunds WHERE transaction_id=?', (key, key)).fetchone():
                raise Problem('收银账目必须在销售订单中办理退单，不能单独作废')
            if db.execute('SELECT 1 FROM transactions WHERE refund_of=? AND voided_at IS NULL', (key,)).fetchone():
                raise Problem('这笔支出存在退款，须先作废关联退款')
            if db.execute('SELECT 1 FROM movements WHERE transaction_id=?', (key,)).fetchone():
                raise Problem('该账目关联库存流水，请到库存管理撤销原流水；库存与关联收支将同时撤销')
            db.execute('UPDATE transactions SET voided_at=? WHERE id=?', (now(), key))
            self._event(db, 'transaction', key, 'voided', {'before': row, 'after': dict(self._row(db, 'transactions', key))})
        return self._finish({'ok': True})

    def transactions(self, month=None, search='', limit=None, offset=0):
        if limit is not None and (type(limit) is not int or not 1 <= limit <= 5000):
            raise Problem('读取笔数应为1至5000')
        if type(offset) is not int or offset < 0 or (offset and limit is None):
            raise Problem('分页位置无效')
        where, args = ['1=1'], []
        if month:
            if not re.fullmatch(r'\d{4}-\d{2}', month):
                raise Problem('月份格式错误')
            try:
                datetime.strptime(month, '%Y-%m')
            except ValueError as exc:
                raise Problem('月份格式错误') from exc
            where.append('t.occurred_at>=? AND t.occurred_at<?')
            args.extend([month + '-01', month + '-32'])
        if search:
            where.append('(t.purpose LIKE ? OR t.merchant LIKE ? OR t.reference LIKE ?)')
            args.extend(['%' + search + '%'] * 3)
        with self.connect() as db:
            rows = db.execute('''SELECT t.*,c.name AS category_name,a.name AS account_name,
                   a2.name AS to_account_name,COALESCE(r.refunded_cents,0) AS refunded_cents FROM transactions t
                   LEFT JOIN categories c ON c.id=t.category_id JOIN accounts a ON a.id=t.account_id
                   LEFT JOIN accounts a2 ON a2.id=t.to_account_id
                   LEFT JOIN (SELECT refund_of,SUM(amount_cents) AS refunded_cents FROM transactions
                       WHERE refund_of IS NOT NULL AND voided_at IS NULL GROUP BY refund_of) r ON r.refund_of=t.id WHERE ''' + ' AND '.join(where) + ' ORDER BY t.occurred_at DESC,t.created_at DESC,t.id DESC' + (' LIMIT ? OFFSET ?' if limit is not None else ''), args + ([limit, offset] if limit is not None else [])).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item['amount'] = money(row['amount_cents'])
                result.append(item)
            return result

    def summary(self, month):
        transactions = [x for x in self.transactions(month) if not x['voided_at']]
        income = sum(x['amount_cents'] for x in transactions if x['kind'] == 'income')
        sale_refunds = sum(x['amount_cents'] for x in transactions if x['kind'] == 'sale_refund')
        income -= sale_refunds
        gross = sum(x['amount_cents'] for x in transactions if x['kind'] == 'expense')
        refunds = sum(x['amount_cents'] for x in transactions if x['kind'] == 'refund')
        categories, days = {}, {}
        for row in transactions:
            day = days.setdefault(row['occurred_at'][:10], {'income_cents': 0, 'expense_cents': 0})
            if row['kind'] in ['income', 'sale_refund']:
                day['income_cents'] += row['amount_cents'] * (-1 if row['kind'] == 'sale_refund' else 1)
            if row['kind'] in ['expense', 'refund']:
                amount = row['amount_cents'] * (-1 if row['kind'] == 'refund' else 1)
                day['expense_cents'] += amount
                key = row['category_name'] or '未分类'
                categories[key] = categories.get(key, 0) + amount
        with self.connect() as db:
            pending = db.execute("SELECT count(*) FROM drafts WHERE status='review'").fetchone()[0]
            low = db.execute('SELECT count(*) FROM products WHERE stock_milli<=minimum_milli').fetchone()[0]
        return {'income_cents': income, 'sale_refund_cents': sale_refunds, 'gross_expense_cents': gross, 'refund_cents': refunds,
                'expense_cents': gross-refunds, 'net_cents': income-gross+refunds,
                'transaction_count': len(transactions), 'pending_count': pending, 'low_stock_count': low,
                'categories': [{'name': k, 'amount_cents': v} for k, v in sorted(categories.items(), key=lambda x: -x[1])],
                'days': [{'date': k, **v} for k, v in sorted(days.items())]}

    @staticmethod
    def _day_scope(day, scope):
        if not isinstance(day, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
            raise Problem('日期格式应为 YYYY-MM-DD')
        try:
            datetime.strptime(day, '%Y-%m-%d')
        except ValueError as exc:
            raise Problem('日期无效') from exc
        return day, text(scope or 'all', 100)

    def _daily_actual(self, db, day, scope):
        if scope != 'all':
            self._row(db, 'accounts', scope)
        rows = db.execute("""SELECT kind,COALESCE(SUM(amount_cents),0) AS total,COUNT(*) AS count
            FROM transactions WHERE occurred_at>=? AND occurred_at<? AND voided_at IS NULL """ +
            ('AND account_id=? ' if scope != 'all' else '') + 'GROUP BY kind',
            (day, day + 'Z', scope) if scope != 'all' else (day, day + 'Z')).fetchall()
        values = {r['kind']: r['total'] for r in rows}
        return {'income': values.get('income', 0) - values.get('sale_refund', 0),
                'expense': values.get('expense', 0) - values.get('refund', 0)}, sum(r['count'] for r in rows if r['kind'] != 'transfer')

    def daily_check(self, day, scope='all'):
        day, scope = self._day_scope(day, scope)
        with self.connect() as db:
            # One read transaction keeps totals and saved reconciliation consistent under concurrent writes.
            db.execute('BEGIN')
            actual, count = self._daily_actual(db, day, scope)
            saved = {r['kind']: dict(r) for r in db.execute('SELECT * FROM reconciliations WHERE day=? AND scope=?', (day, scope))}
        results = []
        for kind in ['income', 'expense']:
            row = saved.get(kind)
            expected = row['amount_cents'] if row else None
            results.append({'kind': kind, 'actual_cents': actual[kind], 'expected_cents': expected,
                'difference_cents': actual[kind] - expected if expected is not None else None,
                'matched': actual[kind] == expected if expected is not None else None,
                'version': row['version'] if row else 0, 'note': row['note'] if row else '',
                'updated_at': row['updated_at'] if row else None})
        return {'day': day, 'scope': scope, 'transaction_count': count, 'checks': results}

    def save_daily_check(self, value):
        day, scope = self._day_scope(value.get('day'), value.get('scope', 'all'))
        kind = value.get('kind')
        if kind not in ['income', 'expense']:
            raise Problem('请选择收入或净支出进行核对')
        amount = scaled(value.get('amount'), allow_zero=True, signed=True)
        note = text(value.get('note'))
        version = value.get('version')
        if type(version) is not int or version < 0:
            raise Problem('缺少核对版本，请重新打开每日核对')
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            actual, count = self._daily_actual(db, day, scope)
            previous = db.execute('SELECT * FROM reconciliations WHERE day=? AND scope=? AND kind=?', (day, scope, kind)).fetchone()
            if version != (previous['version'] if previous else 0):
                raise Problem('另一台设备已修改核对总额，请刷新后再核对')
            key = previous['id'] if previous else identifier()
            data = {'id': key, 'day': day, 'scope': scope, 'kind': kind, 'amount_cents': amount,
                    'note': note, 'version': version+1, 'updated_at': now()}
            db.execute("""INSERT INTO reconciliations VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(day,scope,kind)
                DO UPDATE SET amount_cents=excluded.amount_cents,note=excluded.note,
                version=excluded.version,updated_at=excluded.updated_at""", tuple(data.values()))
            self._event(db, 'reconciliation', key, 'total_checked', {'before': dict(previous) if previous else None, 'after': data,
                'observed_actual_cents': actual[kind], 'observed_transaction_count': count})
        return self._finish({'ok': True})

    def save_receipt(self, filename, content):
        if len(content) > 20 * 1024 * 1024:
            raise Problem('单张图片不能超过20MB')
        if content.startswith(b'\x89PNG\r\n\x1a\n'):
            ext = '.png'
        elif content.startswith(b'\xff\xd8\xff'):
            ext = '.jpg'
        elif content.startswith(b'RIFF') and content[8:12] == b'WEBP':
            ext = '.webp'
        else:
            raise Problem('请上传 PNG、JPG 或 WebP 原图')
        sha = hashlib.sha256(content).hexdigest()
        with self.lock:
            with self.connect() as db:
                existing = db.execute('SELECT id FROM receipts WHERE sha256=?', (sha,)).fetchone()
                if existing:
                    return {'id': existing['id'], 'duplicate_file': True}
                key = identifier()
                path = self.root / 'receipts' / (sha + ext)
                path.write_bytes(content)
                shutil.copy2(path, self.root / 'backups/receipts' / path.name)
                data = {'id': key, 'sha256': sha, 'filename': Path(filename).name[:200], 'extension': ext,
                        'created_at': now(), 'status': 'queued'}
                db.execute('INSERT INTO receipts(id,sha256,filename,extension,created_at,status) VALUES(?,?,?,?,?,?)', tuple(data.values()))
                self._event(db, 'receipt', key, 'uploaded', data)
            return self._finish({'id': key, 'duplicate_file': False})

    def receipt_path(self, key):
        with self.connect() as db:
            row = self._row(db, 'receipts', key)
            return self.root / 'receipts' / (row['sha256'] + row['extension'])

    def receipt(self, key):
        with self.connect() as db:
            row = dict(self._row(db, 'receipts', key))
            row['drafts'] = [{**dict(d), 'data': json.loads(d['data'])}
                             for d in db.execute('SELECT * FROM drafts WHERE receipt_id=?', (key,))]
            return row

    def receipts(self):
        with self.connect() as db:
            return [dict(x) for x in db.execute('SELECT r.*,(SELECT count(*) FROM drafts d WHERE d.receipt_id=r.id AND status=\'review\') AS pending FROM receipts r ORDER BY created_at DESC')]

    def set_ocr(self, key, raw, drafts, error=''):
        with self.lock, self.connect() as db:
            self._row(db, 'receipts', key)
            if db.execute('SELECT 1 FROM drafts WHERE receipt_id=?', (key,)).fetchone():
                raise Problem('这张图已有审核条目，不能重复识别覆盖')
            db.execute('UPDATE receipts SET ocr_text=?,status=?,error=? WHERE id=?',
                       (raw, 'review' if drafts else 'error', error, key))
            for draft in drafts:
                key_d = identifier()
                db.execute('INSERT INTO drafts VALUES(?,?,?,?)', (key_d, key, json.dumps(draft, ensure_ascii=False), 'review'))
            self._event(db, 'receipt', key, 'recognized', {'text': raw, 'drafts': drafts, 'error': error})
        return self._finish({'ok': True})

    def products(self):
        with self.connect() as db:
            return [{**dict(row), 'stock': quantity(row['stock_milli']), 'minimum': quantity(row['minimum_milli']),
                     'low': row['stock_milli'] <= row['minimum_milli']}
                    for row in db.execute('''SELECT p.*,(SELECT COUNT(*) FROM movements m
                        WHERE m.product_id=p.id) AS stock_version FROM products p ORDER BY name''')]

    def add_product(self, value):
        name, unit = text(value.get('name'), 100), text(value.get('unit') or '件', 20)
        if not name:
            raise Problem('请输入商品或原料名称')
        minimum = scaled(value.get('minimum') or '0', 1000, allow_zero=True)
        key, sku = identifier(), text(value.get('sku'), 100) or ('P-' + secrets.token_hex(4).upper())
        with self.lock, self.connect() as db:
            try:
                db.execute('INSERT INTO products VALUES(?,?,?,?,?,?,?)', (key, name, sku, unit, 0, minimum, now()))
            except sqlite3.IntegrityError as exc:
                raise Problem('商品编码已存在') from exc
            self._event(db, 'product', key, 'created', dict(self._row(db, 'products', key)))
        return self._finish({'id': key})

    def add_movement(self, value):
        idem = text(value.get('idempotency_key'), 100)
        if not idem:
            raise Problem('缺少库存请求编号，请重新打开窗口')
        kind = value.get('kind')
        if kind not in ['in', 'out', 'adjust', 'loss', 'customer_return', 'supplier_return']:
            raise Problem('库存动作无效')
        date = when(value.get('occurred_at'))
        qty = scaled(value.get('quantity'), 1000, allow_zero=kind == 'adjust')
        request = {'product_id': value.get('product_id'), 'kind': kind, 'quantity_milli': qty,
                   'occurred_at': date, 'note': text(value.get('note')),
                   'transaction': value.get('transaction') or None}
        if kind == 'adjust':
            version = value.get('expected_stock_version')
            if type(version) is not int or version < 0:
                raise Problem('缺少盘点库存版本，请刷新库存后重新盘点')
            request['expected_stock_version'] = version
        with self.lock:
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                retry = self._movement_retry(db, idem, request)
                if retry:
                    db.rollback()
                    return self._finish(retry)
                product = self._row(db, 'products', value.get('product_id'))
                if kind == 'adjust':
                    current_version = db.execute('SELECT COUNT(*) FROM movements WHERE product_id=?', (product['id'],)).fetchone()[0]
                    if version != current_version:
                        raise Problem('另一台设备或其他操作已变更库存，请刷新后重新盘点')
                    delta = qty - product['stock_milli']
                else:
                    delta = qty * (-1 if kind in ['out', 'loss', 'supplier_return'] else 1)
                if not delta:
                    raise Problem('库存没有变化，无需重复登记')
                if product['stock_milli'] + delta < 0:
                    raise Problem('库存不足，请先核对入库或进行盘点')
                tx = None
                if value.get('transaction'):
                    transaction = value['transaction']
                    if not isinstance(transaction, dict) or transaction.get('kind') != {
                            'in': 'expense', 'out': 'income', 'supplier_return': 'refund'}.get(kind):
                        raise Problem('库存动作与关联收支方向不一致；领用损耗和盘点不新增收入，供应商退款须关联原采购')
                    if kind == 'supplier_return':
                        original = db.execute('''SELECT 1 FROM movements WHERE product_id=?
                            AND kind='in' AND transaction_id=?''', (product['id'], transaction.get('refund_of'))).fetchone()
                        if not original:
                            raise Problem('退供应商的退款必须关联此原料的原采购支出')
                    tx = self._add_transaction(db, value['transaction'])
                    if tx.get('already_saved'):
                        raise Problem('关联账目已存在，不能重复登记库存')
                key = identifier()
                data = {'id': key, 'product_id': product['id'], 'kind': kind, 'delta_milli': delta,
                        'occurred_at': date, 'note': text(value.get('note')),
                        'transaction_id': tx['id'] if tx else None, 'created_at': now(), 'idempotency_key': idem}
                db.execute('INSERT INTO movements VALUES(' + ','.join('?' for _ in data) + ')', tuple(data.values()))
                db.execute('UPDATE products SET stock_milli=stock_milli+? WHERE id=?', (delta, product['id']))
                self._event(db, 'movement', key, 'created', {**data, 'request': request})
            return self._finish({'id': key, 'transaction_id': tx['id'] if tx else None})

    def _movement_retry(self, db, idem, request):
        existing = db.execute('SELECT id,transaction_id FROM movements WHERE idempotency_key=?', (idem,)).fetchone()
        if not existing:
            return None
        event = db.execute("SELECT payload FROM events WHERE entity='movement' AND entity_id=? AND action='created'", (existing['id'],)).fetchone()
        previous = json.loads(event['payload']).get('request') if event else None
        if previous != request:
            raise Problem('此请求编号已保存其他内容，请先核对原库存流水；不能用同一编号修改已保存的记录')
        result = dict(existing) | {'already_saved': True}
        if request.get('action') == 'reverse':
            result['reversed_movement_id'] = request['movement_id']
            result['transaction_id'] = request.get('transaction_id')
        return result

    def reverse_movement(self, key, value):
        idem = text(value.get('idempotency_key'), 100)
        reason = text(value.get('reason'))
        if not idem or not reason:
            raise Problem('请填写撤销原因，并保留原撤销请求编号')
        with self.lock:
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                original = db.execute('SELECT * FROM movements WHERE id=?', (key,)).fetchone()
                if not original:
                    raise Problem('库存流水不存在，请刷新页面')
                request = {'action': 'reverse', 'movement_id': key, 'reason': reason,
                           'transaction_id': original['transaction_id']}
                retry = self._movement_retry(db, idem, request)
                if retry:
                    db.rollback()
                    return self._finish(retry)
                if original['kind'] == 'reversal':
                    raise Problem('撤销记录不能再次撤销，请按实际情况新登记库存')
                if db.execute("SELECT 1 FROM events WHERE entity='movement' AND entity_id=? AND action='reversed'", (key,)).fetchone():
                    raise Problem('这条库存流水已经撤销，请刷新页面')
                if db.execute('''SELECT 1 FROM movements WHERE product_id=? AND kind='adjust'
                        AND rowid>(SELECT rowid FROM movements WHERE id=?)
                        AND id NOT IN (SELECT entity_id FROM events WHERE entity='movement' AND action='reversed')''',
                        (original['product_id'], key)).fetchone():
                    raise Problem('此流水之后已有盘点，请先核对后续盘点记录，不能直接反向更改盘点后的库存')
                product = self._row(db, 'products', original['product_id'])
                delta = -original['delta_milli']
                if product['stock_milli'] + delta < 0:
                    raise Problem('原入库已被领用，撤销会造成负库存；请先核对后续库存流水')
                tx_id = original['transaction_id']
                if tx_id:
                    transaction = dict(self._row(db, 'transactions', tx_id))
                    if transaction['voided_at']:
                        raise Problem('关联账目已作废，请先核对历史记录')
                    if db.execute('SELECT 1 FROM orders WHERE transaction_id=? UNION ALL SELECT 1 FROM order_refunds WHERE transaction_id=?', (tx_id, tx_id)).fetchone():
                        raise Problem('收银账目不能通过库存单独撤销，请在销售订单中处理')
                    if db.execute('SELECT 1 FROM transactions WHERE refund_of=? AND voided_at IS NULL', (tx_id,)).fetchone():
                        raise Problem('原采购存在退款，请先处理关联退款记录')
                    db.execute('UPDATE transactions SET voided_at=? WHERE id=?', (now(), tx_id))
                    self._event(db, 'transaction', tx_id, 'voided', {'before': transaction,
                        'after': dict(self._row(db, 'transactions', tx_id)), 'reason': reason,
                        'reversed_movement_id': key})
                reversal_id = identifier()
                data = {'id': reversal_id, 'product_id': product['id'], 'kind': 'reversal',
                        'delta_milli': delta, 'occurred_at': when(now()), 'note': reason,
                        'transaction_id': None, 'created_at': now(), 'idempotency_key': idem}
                db.execute('INSERT INTO movements VALUES(' + ','.join('?' for _ in data) + ')', tuple(data.values()))
                db.execute('UPDATE products SET stock_milli=stock_milli+? WHERE id=?', (delta, product['id']))
                self._event(db, 'movement', reversal_id, 'created', {**data, 'request': request, 'reversal_of': key})
                self._event(db, 'movement', key, 'reversed', {'reversal_id': reversal_id,
                    'reason': reason, 'transaction_id': tx_id})
            return self._finish({'id': reversal_id, 'transaction_id': tx_id, 'reversed_movement_id': key})

    def movements(self):
        with self.connect() as db:
            reversed_by = {row['entity_id']: json.loads(row['payload'])['reversal_id'] for row in
                           db.execute("SELECT entity_id,payload FROM events WHERE entity='movement' AND action='reversed'")}
            reversal_of = {value: key for key, value in reversed_by.items()}
            return [{**dict(row), 'reversed_by': reversed_by.get(row['id']), 'reversal_of': reversal_of.get(row['id'])}
                    for row in db.execute('SELECT m.*,p.name,p.unit FROM movements m JOIN products p ON p.id=m.product_id ORDER BY occurred_at DESC,created_at DESC LIMIT 1000')]

    def export_csv(self):
        out = io.StringIO(newline='')
        writer = csv.writer(out)
        writer.writerow(['编号', '日期', '类型', '用途', '类目', '金额（元）', '账户', '商户', '交易单号', '原图编号', '是否作废'])
        for row in self.transactions():
            displayed_time = row['occurred_at'][:16] if row['time_precision'] == 'minute' else row['occurred_at'][:10] if row['time_precision'] == 'date' else row['occurred_at']
            # Prevent spreadsheet software from interpreting user text as formulas.
            values = [row['id'], displayed_time, row['kind'], row['purpose'], row['category_name'] or '', row['amount'],
                      row['account_name'], row['merchant'], row['reference'], row['receipt_id'] or '', '是' if row['voided_at'] else '否']
            writer.writerow(["'" + str(v) if str(v).startswith(('=', '+', '-', '@')) else v for v in values])
        return ('\ufeff' + out.getvalue()).encode('utf-8')

    def backup_file(self):
        from .trial import MARKER, SIGNATURE, is_trial
        with self.lock:
            self.recover_backups()
            self._backup()
            out = self.root / 'backups' / ('export-' + identifier() + '.zip')
            with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as archive:
                temporary = self.root / 'backups/database/export.sqlite3'
                with self.connect() as src, closing(sqlite3.connect(temporary)) as dst:
                    src.backup(dst)
                archive.write(temporary, 'ledger.sqlite3')
                for folder in ['receipts', 'backups/records']:
                    for path in (self.root / folder).glob('*'):
                        if path.is_file():
                            archive.write(path, str(path.relative_to(self.root)).replace('\\', '/'))
                for filename in ['legacy-source.json', 'legacy-sources.json']:
                    path = self.root / filename
                    if path.is_file():
                        archive.write(path, filename)
                instructions = '本地账本备份：ledger.sqlite3为数据库；receipts为原始凭证；backups/records为逐笔变更备份。恢复前退出程序，将解压内容放入一个新的数据目录，再使用 --data-dir 指定它。\n'
                if is_trial(self.root):
                    archive.writestr(MARKER, json.dumps(SIGNATURE))
                    instructions += '这是模拟测试账本。请保留.test-ledger.json标记；使用 --test-mode --data-dir 指定恢复目录启动，页面仍应显示模拟账本标识。\n'
                archive.writestr('README.txt', instructions)
            return out

    def export_backup(self):
        path = self.backup_file()
        try:
            return path.read_bytes()
        finally:
            path.unlink(missing_ok=True)

    def backup_status(self):
        with self.connect() as db:
            events = db.execute('SELECT count(*) FROM events').fetchone()[0]
        return {'data_directory': str(self.root), 'record_backup_count': len(list((self.root / 'backups/records').glob('*.json'))),
                'event_count': events, 'receipt_backup_count': len(list((self.root / 'backups/receipts').glob('*'))),
                'database_backups': [p.name for p in sorted((self.root / 'backups/database').glob('*.sqlite3'))]}
