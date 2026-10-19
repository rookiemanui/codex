"""Local OCR and cautious payment screenshot parsing. Nothing is posted here."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import importlib.util
import os
from pathlib import Path
import re
import threading


AMOUNT = re.compile(r'^(?:[¥￥]\s*)?([-−–+]?\s*(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2})(?:\s*元)?$')
FULL_DATE = re.compile(r'(20\d{2})[年/.-](\d{1,2})[月/.-](\d{1,2})[日 ]?\s*(\d{1,2}):(\d{2})(?::(\d{2}))?')
SHORT_DATE = re.compile(r'(\d{1,2})月(\d{1,2})日\s*(\d{1,2}):(\d{2})')
BLOCKS = ('付款成功', '支付成功', '收款成功', '收款到账', '退款成功')


def parse_payment(lines, image_height=None, rules=None):
    """Return separate review drafts. A currency value is never a classification."""
    normalized = []
    for line in lines:
        row = {'text': line} if isinstance(line, str) else dict(line)
        content = str(row.get('text', '')).strip()
        if not content:
            continue
        # Ignore status-bar duplicates without altering the original image.
        if image_height and row.get('y') is not None and row['y'] < image_height * 0.08:
            continue
        labeled_amount = re.match(r'^(?:交易金额|付款金额|支付金额|实付金额|收款金额|退款金额|金额)[：:\s]*(.+)$', content)
        if labeled_amount and AMOUNT.fullmatch(labeled_amount.group(1).replace(' ', '')):
            content = labeled_amount.group(1)
        normalized.append({**row, 'text': content})
    texts = [row['text'] for row in normalized]
    whole = '\n'.join(texts)
    years = set(re.findall(r'(20\d{2})[年/.-]\d{1,2}', whole))
    inferred_year = int(next(iter(years))) if len(years) == 1 else None
    anchors = [i for i, line in enumerate(texts) if line in BLOCKS]
    drafts = []

    def draft_for(amount_index, start, end, anchor=None):
        # Payment cards have their own detail region. Looking back for metadata
        # must not carry a previous card's bus description into the next card.
        block = texts[anchor if anchor is not None else start:end]
        content = texts[amount_index]
        match = AMOUNT.fullmatch(content.replace(' ', ''))
        if not match:
            return
        amount = abs(Decimal(match.group(1).replace(' ', '').replace(',', '').replace('−', '-').replace('–', '-')))
        if not amount:
            return
        notes, merchant, occurred, account, platform = [], '', '', '', ''
        precision = 'unknown'
        prior = texts[max(0, (anchor if anchor is not None else amount_index) - 5):(anchor if anchor is not None else amount_index)]
        ignored = ['查看详情', '全部账单', '支付消息', '服务消息', '当前状态', '支付奖励']
        for value in reversed(prior):
            if not SHORT_DATE.search(value) and not FULL_DATE.search(value) and value not in ignored and not AMOUNT.fullmatch(value.replace(' ', '')) and not re.fullmatch(r'[\d:]+', value):
                merchant = value
                break
        # Detail pages explicitly label payment time. Ride time is not payment time.
        for i, value in enumerate(block):
            if '支付时间' in value or '付款时间' in value or '交易时间' in value:
                combined = value + (block[i+1] if i+1 < len(block) else '')
                match_date = FULL_DATE.search(combined)
                if match_date:
                    parts = [int(x or 0) for x in match_date.groups()]
                    try:
                        occurred = datetime(*parts).isoformat(sep=' ')
                        precision = 'second' if match_date.group(6) is not None else 'minute'
                    except ValueError:
                        notes.append('交易时间需要重新核对')
                    break
        if not occurred:
            for value in reversed(prior):
                short = SHORT_DATE.search(value)
                if short and inferred_year:
                    try:
                        occurred = datetime(inferred_year, *[int(x) for x in short.groups()]).isoformat(sep=' ')
                        precision = 'minute'
                        notes.append('付款通知仅到分钟；年份参考同图日期，00秒为存储占位，请核对')
                    except ValueError:
                        pass
                    break
        joined = '\n'.join(block)
        for i, value in enumerate(block):
            labeled_merchant = re.match(r'^(?:商户简称|商户全称|商户名称|商户)[：:\s]+(.+)$', value)
            if labeled_merchant:
                merchant = labeled_merchant.group(1)
                break
            if value in ['商户简称', '商户全称', '商户名称'] and i + 1 < len(block):
                merchant = block[i+1]
                break
        if '零钱' in joined:
            account, platform = 'wechat', '微信'
        bank = re.search(r'([\u4e00-\u9fff]+银行[^\n]*?(?:\d{4})[)）]?)', joined)
        if bank:
            account = ''
            notes.append('付款账户：' + bank.group(1) + '；请选择或新建相应账户')
        reference = ''
        for i, value in enumerate(block):
            if value.strip() == '交易单号':
                digits = []
                for following in block[i+1:i+4]:
                    if re.fullmatch(r'\d+', following.strip()):
                        digits.append(following.strip())
                    else:
                        break
                reference = ''.join(digits)
                if len(reference) < 15:
                    reference = ''
                break
        kind = 'refund' if '退款成功' in joined else 'income' if any(x in joined for x in ['收款成功', '收款到账']) else 'expense'
        purpose = ''
        category = ''
        if kind == 'expense' and ('公交支撑平台' in joined or re.search(r'BRT\d*号线', joined)):
            purpose, category = '公交乘车', 'transport'
        for rule in rules or []:
            # Never learn category solely from a generic payment platform.
            source = purpose if rule.get('field') == 'purpose' else merchant
            if rule['keyword'] in source:
                category = rule['category_id']
                break
        if not category:
            notes.append('图片没有足够的用途信息，请补充商品或用途后选择类目')
        if not occurred:
            notes.append('未找到可靠付款时间，请填写交易日期')
        if amount_index < len(normalized) and normalized[amount_index].get('confidence', 1) < 0.9:
            notes.append('金额文字识别置信度偏低，务必核对原图')
        drafts.append({'kind': kind, 'amount': f'{amount:.2f}', 'merchant': merchant,
                       'occurred_at': occurred, 'account_id': account, 'category_id': category,
                       'purpose': purpose, 'platform': platform, 'reference': reference,
                       'time_precision': precision,
                       'notes': notes, 'source_amount_text': content, 'review_required': True})

    if len(anchors) > 1:
        for number, anchor in enumerate(anchors):
            end = anchors[number+1] if number+1 < len(anchors) else len(texts)
            amount_index = next((i for i in range(anchor+1, min(end, anchor+6))
                                 if AMOUNT.fullmatch(texts[i].replace(' ', ''))), None)
            if amount_index is not None:
                draft_for(amount_index, max(0, anchor-5), end, anchor)
    else:
        # Detail page: the main currency figure precedes the payment metadata;
        # ignore a repeated status-bar figure when coordinates are unavailable.
        amounts = [i for i, value in enumerate(texts) if AMOUNT.fullmatch(value.replace(' ', ''))]
        if amounts:
            index = amounts[0]
            if len(amounts) > 1 and texts[amounts[0]] == texts[amounts[1]] and amounts[0] < 3:
                index = amounts[1]
            draft_for(index, 0, len(texts), None)
    if not drafts:
        drafts.append({'kind': 'expense', 'amount': '', 'merchant': '', 'occurred_at': '',
                       'account_id': '', 'category_id': '', 'purpose': '', 'reference': '',
                       'platform': '', 'notes': ['未能可靠识别金额，请对照原图填写'], 'review_required': True})
    return drafts


class Recognizer:
    def __init__(self, data_directory):
        self.root = Path(data_directory)
        self.engine = None
        self.lock = threading.Lock()

    def available(self):
        return importlib.util.find_spec('paddleocr') is not None

    def recognize(self, path, rules):
        if not self.available():
            return '', parse_payment([]), '截图识别组件未安装；原图已经保存和备份，可先手工填写'
        with self.lock:
            if self.engine is None:
                cache = os.environ.get('LOCAL_LEDGER_MODEL_CACHE') or os.environ.get('PADDLE_PDX_CACHE_HOME') or str(self.root / 'models')
                model_root = Path(cache) / 'official_models'
                if not all((model_root / model / 'inference.yml').is_file()
                           for model in ['PP-OCRv6_medium_det', 'PP-OCRv6_medium_rec']):
                    raise RuntimeError('本地模型尚未准备，请运行“安装截图识别.bat”或 scripts/prepare_ocr.py')
                os.environ['PADDLE_PDX_CACHE_HOME'] = cache
                os.environ.setdefault('XDG_CACHE_HOME', str(self.root / 'cache'))
                os.environ.setdefault('OMP_NUM_THREADS', '2')
                from paddleocr import PaddleOCR
                self.engine = PaddleOCR(lang='ch', use_doc_orientation_classify=False,
                                        use_doc_unwarping=False, use_textline_orientation=False,
                                        enable_mkldnn=False, cpu_threads=2)
            from PIL import Image
            with Image.open(path) as image:
                height = image.height
            results = list(self.engine.predict(str(path)))
            lines = []
            for result in results:
                words = result.get('rec_texts', [])
                scores = result.get('rec_scores', [1] * len(words))
                boxes = result.get('rec_polys', [])
                for index, word in enumerate(words):
                    line = {'text': word, 'confidence': float(scores[index])}
                    if index < len(boxes):
                        line['y'] = float(min(p[1] for p in boxes[index]))
                    lines.append(line)
            return '\n'.join(x['text'] for x in lines), parse_payment(lines, height, rules), ''
