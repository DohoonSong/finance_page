import calendar
import hashlib
import json
import re
from collections import defaultdict
from datetime import date, datetime, timezone
from uuid import uuid4

from pydantic import ValidationError

from .models import MODELS
from .schema import AppError, SCHEMA
from .store import operation, row_for


def now():
    return datetime.now(timezone.utc).isoformat()


def month_value(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', value):
        raise AppError('월은 YYYY-MM 형식이어야 합니다.', 400)
    try:
        date.fromisoformat(value + '-01')
    except ValueError as exc:
        raise AppError('유효하지 않은 월입니다.', 400) from exc
    return value


def date_value(value):
    try:
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError()
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise AppError('날짜는 YYYY-MM-DD 형식이어야 합니다.', 400) from exc


def month_end(month):
    y, m = map(int, month_value(month).split('-'))
    return f'{month}-{calendar.monthrange(y, m)[1]:02d}'


def active(row):
    return row.get('active') in (True, 'TRUE', 'true', 1)


def latest(rows, keys, cutoff, date_key='date'):
    found = {}
    for r in rows:
        if r[date_key] <= cutoff:
            key = tuple(r[k] for k in keys)
            if key not in found or r[date_key] > found[key][date_key]:
                found[key] = r
    return list(found.values())


def budget_summary(data, month):
    totals = {k: 0 for k in ('income', 'expense', 'investment', 'transfer', 'debt_principal')}
    for row in data['budget_items']:
        if row['month'] == month:
            totals[row['flow_type']] += int(row['amount'])
    return {'month': month, 'currency': 'KRW', **totals, 'budget_balance': totals['income'] - totals['expense'] - totals['investment'] - totals['debt_principal']}


def portfolio_summary(data, cutoff='9999-12-31'):
    accounts = {a['account_id']: a for a in data['accounts']}
    grouped = {}
    for table, date_key in (('holdings', 'date'), ('dividends', 'payment_date'), ('realized_gains', 'trade_date')):
        rows = latest(data[table], ('account_id', 'symbol'), cutoff) if table == 'holdings' else [r for r in data[table] if r[date_key] <= cutoff]
        for r in rows:
            key = r['account_id']
            if key not in grouped:
                grouped[key] = {'account_id': key, 'name': accounts[key]['name'], 'currency': accounts[key]['currency'], 'cost_basis': 0, 'market_value': 0, 'dividend_total': 0, 'realized_profit': 0}
            p = grouped[key]
            for src, dest in ({'cost_basis': 'cost_basis', 'market_value': 'market_value'} if table == 'holdings' else {'net_amount': 'dividend_total'} if table == 'dividends' else {'profit': 'realized_profit'}).items():
                p[dest] += int(r[src])
    for p in grouped.values():
        p['unrealized_profit'] = p['market_value'] - p['cost_basis']
        p['total_profit'] = p['unrealized_profit'] + p['dividend_total'] + p['realized_profit']
        p['simple_return_pct'] = round(p['total_profit'] / p['cost_basis'] * 100, 4) if p['cost_basis'] else None
    return list(grouped.values())


def assets_summary(data, cutoff):
    accounts = {a['account_id']: a for a in data['accounts']}
    items = {a['asset_id']: a for a in data['asset_items']}
    groups = defaultdict(lambda: {'assets_total': 0, 'liabilities_total': 0, 'net_worth': 0})
    details = []
    for r in latest(data['asset_snapshots'], ('asset_id',), cutoff):
        item = items[r['asset_id']]
        account = accounts[item['account_id']]
        if account['type'] in ('brokerage', 'retirement'):
            continue
        groups[account['currency']]['assets_total'] += int(r['amount'])
        details.append({'item_type': 'asset', 'item_id': r['asset_id'], 'amount': r['amount'], 'as_of_date': r['date'], 'source_ref': 'asset_snapshots/' + r['snapshot_id']})
    for r in latest(data['holdings'], ('account_id', 'symbol'), cutoff):
        groups[r['currency']]['assets_total'] += int(r['market_value'])
        details.append({'item_type': 'holding', 'item_id': r['holding_id'], 'amount': r['market_value'], 'as_of_date': r['date'], 'source_ref': 'holdings/' + r['holding_id']})
    for r in latest(data['liability_snapshots'], ('loan_id',), cutoff):
        groups['KRW']['liabilities_total'] += int(r['balance'])
        details.append({'item_type': 'liability', 'item_id': r['loan_id'], 'amount': r['balance'], 'as_of_date': r['date'], 'source_ref': 'liability_snapshots/' + r['snapshot_id']})
    groups['KRW']  # Empty-state base currency.
    for group in groups.values():
        group['net_worth'] = group['assets_total'] - group['liabilities_total']
    return {'date': cutoff, 'by_currency': dict(groups), 'details': details}


def dividend_summary(data, year):
    groups = defaultdict(lambda: {'gross_amount': 0, 'tax_amount': 0, 'net_amount': 0})
    for r in data['dividends']:
        if r['payment_date'][:4] == year:
            p = groups[(r['payment_date'][:7], r['currency'])]
            for key in p:
                p[key] += int(r[key])
    return [{'month': month, 'currency': currency, **v} for (month, currency), v in sorted(groups.items())]


def assert_open(data, table, record):
    closed = [r['month'] for r in effective_closings(data) if r['status'] in ('closed', 'preparing')]
    if not closed:
        return
    field = {'budget_items': 'month', 'asset_snapshots': 'date', 'liability_snapshots': 'date', 'holdings': 'date', 'dividends': 'payment_date', 'realized_gains': 'trade_date'}.get(table)
    if field:
        period = record[field][:7]
        # Snapshot/cumulative changes can affect all later closes, even open gaps.
        blocked = period in closed if table == 'budget_items' else period <= max(closed)
        if blocked:
            raise AppError('마감된 기간에 영향을 주는 변경입니다. 관련 월을 새 리비전으로 재개하세요.', 409, 'period_closed')
    elif table in ('accounts', 'asset_items', 'liabilities'):
        raise AppError('마감 이후 기준정보 수정은 지원하지 않습니다. 새 항목을 추가하세요.', 409, 'period_closed')


def validate_record(table, payload, data, existing=None):
    try:
        validated = MODELS[table].model_validate(payload).model_dump(mode='json')
    except ValidationError as exc:
        message = '; '.join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        raise AppError(message) from exc
    if 'account_id' in validated:
        account = next((r for r in data['accounts'] if r['account_id'] == validated['account_id']), None)
        if not account or not active(account):
            raise AppError('활성 계좌를 선택하세요.')
        if 'currency' in validated and validated['currency'] != account['currency']:
            raise AppError('거래 통화와 계좌 통화가 다릅니다.')
        if table == 'asset_items' and account['type'] in ('brokerage', 'retirement'):
            raise AppError('증권/퇴직연금 계좌는 보유종목으로만 평가합니다. 현금도 CASH 종목으로 입력하세요.')
        if table in ('holdings', 'dividends', 'realized_gains') and account['type'] not in ('brokerage', 'retirement'):
            raise AppError('투자 항목은 증권/퇴직연금 계좌를 사용하세요.')
    for fk, target in (('asset_id', 'asset_items'), ('loan_id', 'liabilities')):
        if fk in validated and not any(r[fk] == validated[fk] and active(r) for r in data[target]):
            raise AppError(f'{fk}: 활성 항목을 찾을 수 없습니다.')
    if table == 'accounts' and existing:
        if any(validated[k] != existing[k] for k in ('currency', 'type')):
            raise AppError('계좌 유형과 통화는 생성 후 변경할 수 없습니다.', 409)
    if table == 'asset_items' and existing and validated['account_id'] != existing['account_id']:
        raise AppError('자산의 소속 계좌는 생성 후 변경할 수 없습니다.', 409)
    unique = {'asset_snapshots': ('date', 'asset_id'), 'liability_snapshots': ('date', 'loan_id'), 'holdings': ('date', 'account_id', 'symbol')}.get(table)
    if unique and any(all(r[k] == validated[k] for k in unique) for r in data[table] if not existing or r[SCHEMA[table][0]] != existing[SCHEMA[table][0]]):
        raise AppError('같은 날짜의 스냅샷이 이미 있습니다. 기존 항목을 수정하세요.', 409, 'duplicate_snapshot')
    return validated


def write_record(repo, rid, table, payload, method='POST', identifier=None, expected_version=None):
    def plan(raw, data):
        existing = None
        if method != 'POST':
            existing = next((r for r in data[table] if r[SCHEMA[table][0]] == identifier), None)
            if not existing:
                raise AppError('항목을 찾을 수 없습니다.', 404)
            if type(expected_version) is not int or expected_version != int(existing['version']):
                raise AppError('다른 변경이 있습니다. 새로고침 후 다시 시도하세요.', 409, 'version_conflict')
            assert_open(data, table, existing)
        elif expected_version != 0 or type(expected_version) is not int:
            raise AppError('생성 요청은 expected_version=0이 필요합니다.', 400)
        if method == 'DELETE':
            return [[operation(table, row_for(raw, table, identifier), {})]], {'deleted': identifier}
        merged = {k: existing[k] for k in MODELS[table].model_fields} if existing else {}
        merged.update(payload)
        record = validate_record(table, merged, data, existing)
        if method != 'POST' or table not in ('accounts', 'asset_items', 'liabilities'):
            assert_open(data, table, record)
        stamp = now()
        record.update({SCHEMA[table][0]: identifier or str(uuid4()), 'created_at': existing.get('created_at', stamp) if existing else stamp, 'updated_at': stamp, 'version': int(existing['version']) + 1 if existing else 1})
        record = {k: record[k] for k in SCHEMA[table]}
        row = row_for(raw, table, identifier) if existing else len(raw[table]) + 1
        return [[operation(table, row, record)]], record
    return repo.mutate(rid, [table, method, identifier, payload, expected_version], plan)


def copy_budget(repo, rid, target, source, expected_version):
    month_value(source)
    if expected_version != 0 or type(expected_version) is not int or source == target:
        raise AppError('다른 원본 월과 expected_version=0을 지정하세요.', 400)
    def plan(raw, data):
        assert_open(data, 'budget_items', {'month': target})
        if any(r['month'] == target for r in data['budget_items']):
            raise AppError('대상 월에 예산이 이미 있습니다.', 409)
        records = []
        for r in data['budget_items']:
            if r['month'] == source:
                records.append({**r, 'budget_id': str(uuid4()), 'month': target, 'created_at': now(), 'updated_at': now(), 'version': 1})
        return [[operation('budget_items', len(raw['budget_items']) + i + 1, r) for i, r in enumerate(records)]], {'items': records}
    return repo.mutate(rid, ['copy', target, source, expected_version], plan)


def close_preview(data, month):
    cutoff = month_end(month)
    assets = assets_summary(data, cutoff)
    portfolio = portfolio_summary(data, cutoff)
    blockers = []
    if any(c != 'KRW' for c in assets['by_currency']) or any(p['currency'] != 'KRW' for p in portfolio):
        blockers.append('환율 정규화 정책이 없어 외화가 포함된 월은 마감할 수 없습니다.')
    for table, rows, key in (('asset_snapshots', data['asset_items'], 'asset_id'), ('liability_snapshots', data['liabilities'], 'loan_id')):
        present = {r[key] for r in latest(data[table], (key,), cutoff)}
        for r in rows:
            if active(r) and r.get('created_at', r.get('start_date', ''))[:10] <= cutoff and r[key] not in present:
                blockers.append(f"{r['name']}: 기준일 이전 잔액이 없습니다.")
    budget = budget_summary(data, month)
    details = assets['details'].copy()
    for r in data['budget_items']:
        if r['month'] == month:
            details.append({'item_type': 'budget', 'item_id': r['budget_id'], 'amount': r['amount'], 'as_of_date': cutoff, 'source_ref': 'budget_items/' + r['budget_id']})
    for table, field, amount in (('dividends', 'payment_date', 'net_amount'), ('realized_gains', 'trade_date', 'profit')):
        for r in data[table]:
            if r[field] <= cutoff:
                details.append({'item_type': table, 'item_id': r[SCHEMA[table][0]], 'amount': r[amount], 'as_of_date': r[field], 'source_ref': table + '/' + r[SCHEMA[table][0]]})
    summary = {**assets['by_currency']['KRW'], 'budget_balance': budget['budget_balance'], 'portfolio_profit': sum(p['total_profit'] for p in portfolio if p['currency'] == 'KRW')}
    digest = hashlib.sha256(json.dumps({'summary': summary, 'details': details, 'portfolio': portfolio}, sort_keys=True).encode()).hexdigest()
    revisions = [int(r['revision']) for r in data['monthly_closings'] if r['month'] == month]
    return {'month': month, 'revision': max(revisions, default=0), 'summary': summary, 'details': details, 'data_hash': digest, 'blockers': blockers, 'can_finalize': not blockers}


def finalize(repo, rid, month, payload):
    def plan(raw, data):
        preview = close_preview(data, month)
        rows = [r for r in data['monthly_closings'] if r['month'] == month]
        current = max(rows, key=lambda r: int(r['revision']), default=None)
        if type(payload.get('expected_version')) is not int or payload['expected_version'] != preview['revision']:
            raise AppError('월마감 리비전이 변경되었습니다.', 409)
        if current and current['status'] == 'closed':
            raise AppError('이미 마감되었습니다. 새 리비전으로 재개하세요.', 409)
        if preview['blockers']:
            raise AppError(' '.join(preview['blockers']), 422)
        if payload.get('data_hash') != preview['data_hash']:
            raise AppError('미리보기 이후 데이터가 바뀌었습니다. 미리보기를 갱신하세요.', 409)
        cid = current['closing_id'] if current else str(uuid4())
        revision = int(current['revision']) if current else 1
        closing = {'closing_id': cid, 'month': month, 'status': 'preparing', 'revision': revision, **preview['summary'], 'data_hash': preview['data_hash'], 'closed_at': '', 'created_at': current['created_at'] if current else now()}
        row = row_for(raw, 'monthly_closings', cid) if current else len(raw['monthly_closings']) + 1
        details = [operation('closing_details', len(raw['closing_details']) + i + 1, {'closing_id': cid, 'revision': revision, **d}) for i, d in enumerate(preview['details'])]
        closed = {**closing, 'status': 'closed', 'closed_at': now()}
        return [[operation('monthly_closings', row, {**closing, 'status': 'open'})], [operation('monthly_closings', row, closing)], details, [operation('monthly_closings', row, closed)]], closed
    return repo.mutate(rid, ['finalize', month, payload], plan)


def reopen(repo, rid, month, payload):
    def plan(raw, data):
        rows = [r for r in data['monthly_closings'] if r['month'] == month]
        current = max(rows, key=lambda r: int(r['revision']), default=None)
        if not current or current['status'] != 'closed' or type(payload.get('expected_version')) is not int or payload['expected_version'] != int(current['revision']):
            raise AppError('재개할 마감 리비전이 일치하지 않습니다.', 409)
        record = {**current, 'closing_id': str(uuid4()), 'status': 'open', 'revision': int(current['revision']) + 1, 'closed_at': '', 'created_at': now()}
        return [[operation('monthly_closings', len(raw['monthly_closings']) + 1, record)]], record
    return repo.mutate(rid, ['reopen', month, payload], plan)


def effective_closings(data):
    latest_rows = {}
    for r in data['monthly_closings']:
        if r['month'] not in latest_rows or int(r['revision']) > int(latest_rows[r['month']]['revision']):
            latest_rows[r['month']] = r
    return list(latest_rows.values())
