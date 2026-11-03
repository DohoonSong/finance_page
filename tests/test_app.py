import re
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from finance.schema import AppError
from finance.services import write_record
from finance.store import Repository


def add(send, resource, **fields):
    r = send('/' + resource, {'expected_version': 0, **fields})
    assert r.status_code == 201, r.json
    return r.json['data']


def budget(send, **kwargs):
    return add(send, 'budgets', month='2026-10', category='생활', item='식비', amount=100, flow_type='expense', **kwargs)


def account(send, currency='KRW', kind='bank'):
    return add(send, 'accounts', name='테스트 계좌', type=kind, currency=currency)


def test_auth_required(app):
    c = app.test_client()
    assert c.get('/api/v1/accounts').status_code == 401
    assert c.post('/api/v1/accounts', json={}).status_code == 401
    assert c.get('/dashboard').status_code == 302


def test_csrf_and_logout(client, send):
    assert client.post('/api/v1/accounts', json={}).status_code == 400
    assert client.get('/logout').status_code == 404
    assert client.get('/dashboard').headers['X-Frame-Options'] == 'DENY'


@pytest.mark.parametrize('page', ['dashboard', 'budgets', 'assets', 'investments', 'dividends', 'history', 'settings'])
def test_pages(client, page):
    r = client.get('/' + page)
    assert r.status_code == 200
    assert 'csrf-token' in r.text


@pytest.mark.parametrize('path', ['/dashboard?month=2026-10', '/accounts', '/budgets?month=2026-10', '/budget-summary?month=2026-10', '/assets?date=2026-10-31', '/liabilities', '/holdings', '/portfolio-summary', '/dividends?year=2026', '/dividend-summary?year=2026', '/closings', '/closings/2026-10', '/net-worth/history', '/settings', '/backup'])
def test_empty_api(client, path):
    r = client.get('/api/v1' + path)
    assert r.status_code == 200, r.json


def test_header_mismatch(client, backend):
    backend.cells['accounts'][0][0] = 'wrong_id'
    assert client.get('/api/v1/accounts').status_code == 503


def test_read_only(app, send):
    app.config['WRITES_ENABLED'] = False
    assert send('/accounts', {}).status_code == 403


def test_create_idempotent_conflict_and_version(send, client):
    rid = str(uuid4())
    payload = {'expected_version': 0, 'name': '은행', 'type': 'bank', 'currency': 'KRW'}
    one = send('/accounts', payload, rid=rid)
    assert send('/accounts', payload, rid=rid).json == one.json
    assert send('/accounts', {**payload, 'name': '다름'}, rid=rid).status_code == 409
    row = one.json['data']
    assert send('/accounts/' + row['account_id'], {'name': '수정', 'expected_version': 1}, 'PATCH').json['data']['version'] == 2
    assert send('/accounts/' + row['account_id'], {'name': '충돌', 'expected_version': 1}, 'PATCH').status_code == 409
    assert len(client.get('/api/v1/accounts').json['data']) == 1


@pytest.mark.parametrize('amount', [-1, 1.5, '100', True, 10**16])
def test_integer_money_validation(send, amount):
    assert send('/budgets', {'expected_version': 0, 'month': '2026-10', 'category': '소비', 'item': '검사', 'amount': amount, 'flow_type': 'expense'}).status_code == 422


def test_date_and_filter_validation(send, client):
    assert client.get('/api/v1/dashboard?month=bad').status_code == 400
    assert client.get('/api/v1/dashboard?month=0000-01').status_code == 400
    assert client.get('/api/v1/assets?date=2026-02-30').status_code == 400
    assert client.get('/api/v1/dividends?year=bad').status_code == 400
    assert send('/budgets', {'expected_version': 0, 'month': '2026-13', 'category': '소비', 'item': '검사', 'amount': 1, 'flow_type': 'expense'}).status_code == 422


def test_taxonomy_copy_and_delete(send, client):
    for flow, amount in [('income', 1000), ('expense', 100), ('investment', 200), ('transfer', 999), ('debt_principal', 50)]:
        add(send, 'budgets', month='2026-10', category='기본', item=flow, amount=amount, flow_type=flow)
    summary = client.get('/api/v1/budget-summary?month=2026-10').json['data']
    assert summary['expense'] == 100 and summary['budget_balance'] == 650
    copied = send('/budgets/2026-11/copy', {'expected_version': 0, 'source_month': '2026-10'})
    assert len(copied.json['data']['items']) == 5
    assert send('/budgets/2026-11/copy', {'expected_version': 0, 'source_month': '2026-10'}).status_code == 409
    row = copied.json['data']['items'][0]
    assert send('/budgets/' + row['budget_id'], {'expected_version': 1}, 'DELETE').status_code == 200


def test_latest_snapshots_currency_and_dividend_no_double_count(send, client):
    a = account(send, kind='brokerage')
    for day, market in [('2026-10-01', 1200), ('2026-10-31', 1500)]:
        add(send, 'holdings', date=day, account_id=a['account_id'], symbol='AAA', name='주식', cost_basis=1000, market_value=market, currency='KRW')
    add(send, 'dividends', payment_date='2026-10-15', account_id=a['account_id'], symbol='AAA', gross_amount=100, tax_amount=15, net_amount=85, currency='KRW')
    add(send, 'realized-gains', trade_date='2026-10-15', account_id=a['account_id'], symbol='AAA', profit=-20, currency='KRW')
    usd = account(send, currency='USD', kind='brokerage')
    add(send, 'holdings', date='2026-10-31', account_id=usd['account_id'], symbol='BBB', name='미국', cost_basis=10, market_value=12, currency='USD')
    data = client.get('/api/v1/dashboard?month=2026-10').json['data']
    assert data['assets']['by_currency']['KRW']['net_worth'] == 1500
    assert data['assets']['by_currency']['USD']['net_worth'] == 12
    assert data['portfolio'][0]['total_profit'] == 565
    assert send('/closings/2026-10/preview', {}).json['data']['can_finalize'] is False
    assert send('/asset-items', {'expected_version': 0, 'name': '중복', 'category': 'cash', 'account_id': a['account_id']}).status_code == 422


def test_snapshot_duplicates_and_net_dividend_validation(send):
    a = account(send, kind='brokerage')
    fields = dict(date='2026-10-01', account_id=a['account_id'], symbol='A', name='A', cost_basis=10, market_value=12, currency='KRW')
    add(send, 'holdings', **fields)
    assert send('/holdings', {'expected_version': 0, **fields}).status_code == 409
    assert send('/dividends', {'expected_version': 0, 'payment_date': '2026-10-01', 'account_id': a['account_id'], 'symbol': 'A', 'gross_amount': 100, 'tax_amount': 15, 'net_amount': 100, 'currency': 'KRW'}).status_code == 422


def test_asset_debt_snapshots(send, client):
    a = account(send)
    item = add(send, 'asset-items', name='예금', account_id=a['account_id'], category='deposit')
    add(send, 'asset-snapshots', date='2026-10-01', asset_id=item['asset_id'], amount=2000)
    add(send, 'asset-snapshots', date='2026-10-31', asset_id=item['asset_id'], amount=2500)
    loan = add(send, 'liabilities', name='대출', original_principal=1000, annual_rate=0.04012, start_date='2026-01-01', maturity_date='2027-01-01')
    add(send, 'liability-snapshots', date='2026-10-31', loan_id=loan['loan_id'], balance=800)
    assert client.get('/api/v1/assets?date=2026-10-31').json['data']['by_currency']['KRW']['net_worth'] == 1700


def test_close_revision_hash_and_immutability(send, client):
    row = budget(send)
    preview = send('/closings/2026-10/preview', {}).json['data']
    rid = str(uuid4())
    request = {'expected_version': 0, 'data_hash': preview['data_hash']}
    closed = send('/closings/2026-10/finalize', request, rid=rid)
    assert closed.status_code == 200, closed.json
    assert closed.json['data']['status'] == 'closed'
    assert send('/closings/2026-10/finalize', request, rid=rid).json == closed.json
    assert send('/budgets/' + row['budget_id'], {'amount': 200, 'expected_version': 1}, 'PATCH').status_code == 409
    assert send('/closings/2026-10/reopen', {'expected_version': 1}).status_code == 200
    assert send('/budgets/' + row['budget_id'], {'amount': 200, 'expected_version': 1}, 'PATCH').status_code == 200
    preview = send('/closings/2026-10/preview', {}).json['data']
    assert send('/closings/2026-10/finalize', {'expected_version': 2, 'data_hash': preview['data_hash']}).status_code == 200
    history = client.get('/api/v1/closings/2026-10').json['data']
    assert [r['revision'] for r in history['items']] == [1, 2]
    assert len(history['details']) == 2
    assert len(client.get('/api/v1/net-worth/history').json['data']) == 1


def test_preview_stale_rejected(send):
    row = budget(send)
    old = send('/closings/2026-10/preview', {}).json['data']
    send('/budgets/' + row['budget_id'], {'amount': 200, 'expected_version': 1}, 'PATCH')
    assert send('/closings/2026-10/finalize', {'expected_version': 0, 'data_hash': old['data_hash']}).status_code == 409


def test_ambiguous_write_recovered_without_duplicate(send, client, backend):
    original = backend.apply
    count = 0
    def fail_once(ops):
        nonlocal count
        original(ops)
        count += 1
        if count == 1:
            raise TimeoutError('response lost after write')
    backend.apply = fail_once
    rid = str(uuid4())
    payload = {'expected_version': 0, 'name': '재시도', 'type': 'bank'}
    assert send('/accounts', payload, rid=rid).status_code == 503
    assert send('/accounts', payload, rid=rid).status_code == 201
    assert len(client.get('/api/v1/accounts').json['data']) == 1


def test_close_partial_failure_recovered(send, client, backend):
    budget(send)
    preview = send('/closings/2026-10/preview', {}).json['data']
    original, count = backend.apply, 0
    def fail_details(ops):
        nonlocal count
        count += 1
        original(ops)
        if count == 3:
            raise TimeoutError('details applied; connection lost')
    backend.apply = fail_details
    rid = str(uuid4())
    payload = {'expected_version': 0, 'data_hash': preview['data_hash']}
    assert send('/closings/2026-10/finalize', payload, rid=rid).status_code == 503
    assert send('/closings/2026-10/finalize', payload, rid=rid).status_code == 200
    data = client.get('/api/v1/closings/2026-10').json['data']
    assert len(data['items']) == len(data['details']) == 1


def test_serialized_versions_and_cross_worker_cache(app, send, backend, tmp_path):
    row = account(send)
    repo = app.extensions['repository']
    second = Repository(backend, tmp_path)
    assert second.read()['accounts'][0]['version'] == 1
    def update(name):
        try:
            return write_record(repo, str(uuid4()), 'accounts', {'name': name}, 'PATCH', row['account_id'], 1)
        except AppError as exc:
            return exc.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, ['A', 'B']))
    assert sum(r == 409 for r in results) == 1
    assert second.read()['accounts'][0]['version'] == 2


def test_settings_version(send, client):
    current = client.get('/api/v1/settings').json['data']
    assert send('/settings', {'expected_version': current['version'], 'display_name': '나의 기록'}, 'PATCH').status_code == 200
    assert send('/settings', {'expected_version': current['version'], 'display_name': '충돌'}, 'PATCH').status_code == 409


def test_login_rate_limit(app):
    c = app.test_client()
    token = re.search(r'name="csrf_token" value="([^"]+)"', c.get('/login').text)[1]
    for _ in range(10):
        assert c.post('/login', data={'username': 'owner', 'password': 'wrong', 'csrf_token': token}).status_code == 401
    assert c.post('/login', data={'username': 'owner', 'password': 'wrong', 'csrf_token': token}).status_code == 429


def test_formula_plan_is_offline(app, backend):
    before = backend.read()
    result = app.test_cli_runner().invoke(args=['install-formulas'])
    assert result.exit_code == 0
    assert 'calc_budget' in result.output and 'SORTN' in result.output
    assert before == backend.read()
