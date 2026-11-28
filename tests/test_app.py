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


@pytest.mark.parametrize('page', ['dashboard', 'budgets', 'actuals', 'assets', 'investments', 'dividends', 'history', 'settings'])
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


@pytest.mark.parametrize('amount', [-9_000_000_000_001, 1.5, '100', True, 10**16])
def test_integer_money_validation(send, amount):
    assert send('/budgets', {'expected_version': 0, 'month': '2026-10', 'category': '소비', 'item': '검사', 'amount': amount, 'flow_type': 'expense'}).status_code == 422


def test_negative_monthly_budget_amount_is_saved_and_applied(send, client):
    fields = dict(month='2026-10', category='주거', flow_type='debt_principal')
    row = add(send, 'budgets', item='전세 상환금', amount=-200, **fields)
    assert row['amount'] == -200
    updated = send('/budgets/' + row['budget_id'], {'expected_version': 1, 'amount': -250}, 'PATCH')
    assert updated.json['data']['amount'] == -250
    summary = client.get('/api/v1/budget-summary?month=2026-10').json['data']
    assert summary['debt_principal'] == -250
    assert summary['budget_balance'] == 250
    lease = client.get('/api/v1/assets?date=2026-10-31').json['data']['lease_budget']
    assert lease['lease_repayment'] == 250
    assert lease['lease_total'] is None
    copied = send('/budgets/2026-11/copy', {'expected_version': 0, 'source_month': '2026-10'})
    assert copied.json['data']['items'][0]['amount'] == -250


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


def test_budget_summary_category_filter(send, client):
    budget(send)
    add(send, 'budgets', month='2026-10', category='교통', item='기차', amount=250, flow_type='expense')
    result = client.get('/api/v1/budget-summary', query_string={'month': '2026-10', 'category': '생활'})
    assert result.json['data']['expense'] == 100


def test_toss_categories_persist_and_filter_without_reusing_old_snapshots(send, client):
    toss = add(send, 'accounts', name='주계좌', institution='토스증권', type='brokerage', currency='KRW')
    other = account(send, kind='brokerage')
    fields = dict(account_id=toss['account_id'], symbol='AAA', currency='KRW')
    for day, value, category in [('01', 1200, 'ETF'), ('30', 1500, '테크빅')]:
        add(send, 'holdings', date=f'2026-10-{day}', name='주식', cost_basis=1000,
            market_value=value, investment_category=category, **fields)
    dividend = dict(payment_date='2026-10-15', gross_amount=100, tax_amount=15, net_amount=85, **fields)
    legacy = add(send, 'dividends', **dividend)
    assert legacy['investment_category'] == ''
    path = '/dividends/' + legacy['dividend_id']
    assert send(path, {'expected_version': 1, 'investment_category': '미등록'}, 'PATCH').status_code == 422
    assert send(path, {'expected_version': 1, 'investment_category': 'ETF'}, 'PATCH').status_code == 200
    assert send(path, {'expected_version': 2, 'note': '분류 유지'}, 'PATCH').status_code == 200
    assert send('/dividends', {
        **dividend, 'account_id': other['account_id'], 'expected_version': 0, 'investment_category': 'ETF',
    }).status_code == 422
    add(send, 'dividends', **{**dividend, 'account_id': other['account_id']})
    add(send, 'realized-gains', trade_date='2026-10-15', profit=20, investment_category='ETF', **fields)
    query = {'account_id': toss['account_id'], 'investment_category': 'ETF'}
    assert client.get('/api/v1/portfolio-summary', query_string=query).json['data'][0]['market_value'] == 0
    assert client.get('/api/v1/portfolio-summary', query_string=query).json['data'][0]['total_profit'] == 105
    assert client.get('/api/v1/dividend-summary', query_string={**query, 'year': '2026'}).json['data'][0]['net_amount'] == 85
    rows = client.get('/api/v1/dividends?year=2026').json['data']
    assert [row['investment_category'] for row in rows] == ['ETF', '']
    assert not client.get('/api/v1/settings').json['data']['items']
    assert client.get('/api/v1/backup').json['tables']['settings'][0]['value'] == 'ETF'
    assert send(path, {'expected_version': 3, 'investment_category': ''}, 'PATCH').status_code == 200
    assert client.get('/api/v1/dividend-summary', query_string={**query, 'year': '2026'}).json['data'] == []


def test_budget_category_levels_persist_copy_and_delete(send, client):
    item = budget(send, category_2=' 식비 ', category_3='외식')
    assert (item['category'], item['category_2'], item['category_3']) == ('생활', '식비', '외식')
    path = '/budgets/' + item['budget_id']
    assert send(path, {'expected_version': 1, 'category_2': ''}, 'PATCH').status_code == 422
    assert send(path, {'expected_version': 1, 'note': '메모'}, 'PATCH').json['data']['category_3'] == '외식'
    copied = send('/budgets/2026-11/copy', {'source_month': '2026-10', 'expected_version': 0})
    assert copied.status_code == 201
    result = client.get('/api/v1/budgets?month=2026-11').json['data'][0]
    assert (result['category_2'], result['category_3']) == ('식비', '외식')
    assert send(path, {'expected_version': 2}, 'DELETE').status_code == 200
    settings = client.get('/api/v1/backup').json['tables']['settings']
    assert [row['key'] for row in settings] == ['budget_category/' + result['budget_id']]
    assert not client.get('/api/v1/settings').json['data']['items']


def test_holding_group_and_dividend_order_with_symbol_and_month_filters(send, client):
    a = account(send, kind='brokerage')
    account_id = a['account_id']
    holding = add(send, 'holdings', date='2026-10-01', account_id=account_id, symbol='AAA',
                  name='실제 이름', cost_basis=100, market_value=150, currency='KRW',
                  portfolio_group='장기 투자')
    assert holding['portfolio_group'] == '장기 투자'
    assert client.get('/api/v1/holdings').json['data'][0]['portfolio_group'] == '장기 투자'
    grouped = client.get('/api/v1/portfolio-summary', query_string={
        'account_id': account_id, 'investment_category': '장기 투자',
    }).json['data']
    assert grouped[0]['market_value'] == 150

    for date, symbol, amount in [('2026-10-03', 'AAA', 30), ('2026-11-01', 'AAA', 50), ('2026-10-10', 'BBB', 10)]:
        add(send, 'dividends', payment_date=date, account_id=account_id, symbol=symbol,
            gross_amount=amount, tax_amount=0, net_amount=amount, currency='KRW')
    rows = client.get('/api/v1/dividends?year=2026').json['data']
    assert [row['payment_date'] for row in rows] == ['2026-11-01', '2026-10-10', '2026-10-03']
    summary = client.get('/api/v1/dividend-summary?year=2026&month=2026-10&symbol=AAA').json['data']
    assert [(row['month'], row['net_amount']) for row in summary] == [('2026-10', 30)]


@pytest.mark.parametrize('repayment', [200, -200])
def test_lease_amounts_use_only_selected_months_budget(send, client, repayment):
    add(send, 'budgets', month='2026-11', category='주거', item='전세금', amount=2400, flow_type='expense')
    add(send, 'budgets', month='2026-11', category='주거', item='전세대출', amount=1000, flow_type='income')
    add(send, 'budgets', month='2026-11', category='주거', item='상환', category_2='전세 상환금',
        amount=repayment, flow_type='debt_principal')
    add(send, 'budgets', month='2026-10', category='주거', item='전세대출', amount=900, flow_type='income')
    result = client.get('/api/v1/assets?date=2026-11-30').json['data']
    assert result['lease_budget'] == {'lease_loan': 1000, 'lease_repayment': 200, 'lease_total': 800}
    assert result['by_currency']['KRW']['liabilities_total'] == 0
    assert client.get('/api/v1/assets?date=2026-09-30').json['data']['lease_budget']['lease_total'] is None


def test_dividend_group_is_manual_and_can_coexist_with_toss_category(send, client):
    a = add(send, 'accounts', name='토스증권', type='brokerage', currency='KRW')
    fields = dict(payment_date='2026-11-11', account_id=a['account_id'], symbol='ABC',
                  gross_amount=100, tax_amount=0, net_amount=100, currency='KRW')
    marked = add(send, 'dividends', dividend_group='ISA', investment_category='ETF', **fields)
    unmarked = add(send, 'dividends', **fields)
    rows = client.get('/api/v1/dividends?year=2026').json['data']
    assert {row['dividend_id']: (row['dividend_group'], row['investment_category']) for row in rows} == {
        marked['dividend_id']: ('ISA', 'ETF'), unmarked['dividend_id']: ('', ''),
    }
    total = client.get('/api/v1/dividend-summary?year=2026&dividend_group=ISA').json['data']
    assert total[0]['net_amount'] == 100
    path = '/dividends/' + unmarked['dividend_id']
    assert send(path, {'expected_version': 1, 'dividend_group': '다른 분류'}, 'PATCH').status_code == 422
    assert send(path, {'expected_version': 1, 'dividend_group': '토스증권'}, 'PATCH').status_code == 200
    assert not client.get('/api/v1/settings').json['data']['items']


def test_asset_balance_groups_use_month_end_cost_basis_and_deposit_balances(send, client):
    for name, kind, currency, cost in [
        ('토스증권', 'brokerage', 'KRW', 100),
        ('중개형 ISA', 'brokerage', 'KRW', 200),
        ('하나IRP', 'retirement', 'KRW', 300),
        ('신투 연금저축', 'brokerage', 'KRW', 400),
        ('하나IRP 외화', 'retirement', 'USD', 5),
    ]:
        a = add(send, 'accounts', name=name, type=kind, currency=currency)
        for date, basis in [('2026-11-01', cost), ('2026-12-01', cost + 1)]:
            add(send, 'holdings', date=date, account_id=a['account_id'], symbol='A',
                name='테스트 종목', cost_basis=basis, market_value=basis * 2, currency=currency)
    bank = add(send, 'accounts', name='신한', institution='신한', type='bank', currency='KRW')
    a = add(send, 'asset-items', account_id=bank['account_id'], category='deposit', name='운동화 적금')
    add(send, 'asset-snapshots', date='2026-11-21', asset_id=a['asset_id'], amount=50)
    groups = client.get('/api/v1/assets?date=2026-11-30').json['data']['balance_groups']
    assert {(row['name'], row['currency']): row['amount'] for row in groups} == {
        ('토스증권', 'KRW'): 100, ('ISA', 'KRW'): 200, ('연금저축', 'KRW'): 700,
        ('연금저축', 'USD'): 5, ('주택청약저축', ''): None, ('신한운동화적금', 'KRW'): 50,
    }


def test_dividend_years_and_monthly_account_totals(send, client):
    toss = add(send, 'accounts', name='토스증권', type='brokerage', currency='KRW')
    isa = add(send, 'accounts', name='중개형 ISA', institution='토스증권', type='brokerage', currency='KRW')
    other = account(send, kind='brokerage')
    for date, a, group, amount in [
        ('2025-11-01', toss, '', 50),
        ('2026-11-01', toss, 'ISA', 100),
        ('2026-11-02', isa, '', 200),
        ('2026-11-03', other, '토스증권', 300),
    ]:
        add(send, 'dividends', payment_date=date, account_id=a['account_id'], symbol='AAA',
            gross_amount=amount, tax_amount=0, net_amount=amount, currency='KRW', dividend_group=group)
    assert client.get('/api/v1/dividend-years').json['data'] == ['2026', '2025']
    row = client.get('/api/v1/dividend-summary?year=2026').json['data'][0]
    assert (row['month'], row['gross_amount'], row['toss_gross_amount'], row['isa_gross_amount']) == (
        '2026-11', 600, 100, 200,
    )
    old = client.get('/api/v1/dividend-summary?year=2025').json['data'][0]
    assert (old['month'], old['gross_amount'], old['toss_gross_amount'], old['isa_gross_amount']) == (
        '2025-11', 50, 50, 0,
    )


def test_dividend_batch_is_atomic_and_idempotent(send, client):
    krw = account(send, kind='brokerage')
    usd = account(send, currency='USD', kind='brokerage')
    payload = {'payment_date': '2026-11-03', 'items': [
        {'account_id': krw['account_id'], 'dividend_group': 'ISA', 'gross_amount': 1200},
        {'account_id': usd['account_id'], 'dividend_group': '토스증권', 'gross_amount': 24},
    ]}
    invalid_items = [payload['items'][0], {**payload['items'][1], 'gross_amount': -1}]
    failure = send('/dividends/batch', {**payload, 'items': invalid_items})
    assert failure.status_code == 422
    assert '2번째' in failure.json['error']['message']
    assert client.get('/api/v1/dividends?year=2026').json['data'] == []

    rid = str(uuid4())
    response = send('/dividends/batch', payload, rid=rid)
    assert response.status_code == 201
    assert send('/dividends/batch', payload, rid=rid).json == response.json
    records = client.get('/api/v1/dividends?year=2026').json['data']
    assert [(r['currency'], r['dividend_group'], r['gross_amount'], r['net_amount']) for r in records] == [
        ('KRW', 'ISA', 1200, 1200), ('USD', '토스증권', 24, 24),
    ]
    assert all(r['symbol'] == '' and r['tax_amount'] == 0 for r in records)
    updated = send('/dividends/' + records[0]['dividend_id'], {'expected_version': 1, 'note': '확인'}, 'PATCH')
    assert updated.status_code == 200


def test_holding_amount_batch_is_atomic_and_idempotent(send, client):
    broker = account(send, kind='brokerage')
    holdings = [
        add(send, 'holdings', date='2026-10-01', account_id=broker['account_id'],
            symbol=symbol, name=symbol, cost_basis=100, market_value=200,
            currency='KRW', portfolio_group='장기 투자')
        for symbol in ('AAA', 'BBB')
    ]
    items = [
        {'holding_id': row['holding_id'], 'expected_version': 1,
         'cost_basis': index * 10, 'market_value': index * 20}
        for index, row in enumerate(holdings)
    ]

    def records():
        return client.get('/api/v1/holdings').json['data']

    for extra, status, code in (
        ({'market_value': -1}, 422, None),
        ({'expected_version': 0}, 409, 'version_conflict'),
        ({'holding_id': items[0]['holding_id']}, 422, None),
    ):
        failure = send('/holdings/batch', {'items': [items[0], {**items[1], **extra}]}, 'PATCH')
        assert failure.status_code == status
        if code:
            assert failure.json['error']['code'] == code
        assert all(row['version'] == 1 and row['market_value'] == 200 for row in records())

    rid = str(uuid4())
    response = send('/holdings/batch', {'items': items}, 'PATCH', rid=rid)
    assert response.json['data'] == {'updated': 2}
    assert send('/holdings/batch', {'items': items}, 'PATCH', rid=rid).json == response.json
    assert [(row['cost_basis'], row['market_value'], row['version'], row['portfolio_group'])
            for row in records()] == [(0, 0, 2, '장기 투자'), (10, 20, 2, '장기 투자')]


def test_dividend_amount_batch_is_account_scoped_and_atomic(send, client):
    items = []
    for name in ('토스증권', '토스 ISA', '신투연금저축'):
        broker = add(send, 'accounts', name=name, type='brokerage', currency='KRW')
        row = add(send, 'dividends', account_id=broker['account_id'], payment_date='2026-11-11',
                  gross_amount=100, tax_amount=10, net_amount=90, currency='KRW', dividend_group='ISA')
        items.append({'dividend_id': row['dividend_id'], 'expected_version': 1,
                      'gross_amount': 80, 'tax_amount': 20})

    def update(rows, rid=None):
        return send('/dividends/batch', {'items': rows}, 'PATCH', rid=rid)

    def records():
        return client.get('/api/v1/dividends?year=2026').json['data']

    for rejected in (items[:2], items[2:], [{**items[0], 'tax_amount': 81}]):
        assert update(rejected).status_code == 422
    assert all(row['net_amount'] == 90 and row['version'] == 1 for row in records())
    rid = str(uuid4())
    response = update(items[:1], rid)
    assert response.json['data'] == {'updated': 1}
    assert update(items[:1], rid).json == response.json
    assert update(items[1:2]).status_code == 200
    assert [(row['net_amount'], row['version'], row['dividend_group']) for row in records()] == [
        (60, 2, 'ISA'), (60, 2, 'ISA'), (90, 1, 'ISA')]


def test_dividend_batch_payment_date_respects_closing_and_refreshes_summary(send, client):
    broker = add(send, 'accounts', name='ISA', type='brokerage', currency='KRW')
    dividend = add(send, 'dividends', account_id=broker['account_id'], payment_date='2026-11-11',
                   gross_amount=100, tax_amount=10, net_amount=90, currency='KRW')
    preview = send('/closings/2026-10/preview', {}).json['data']
    assert send('/closings/2026-10/finalize', {
        'expected_version': 0, 'data_hash': preview['data_hash'],
    }).status_code == 200
    item = {'dividend_id': dividend['dividend_id'], 'expected_version': 1,
            'gross_amount': 100, 'tax_amount': 10}

    def change(date):
        return send('/dividends/batch', {'items': [{**item, 'payment_date': date}]}, 'PATCH')

    assert change('2026-11-31').status_code == 422
    blocked = change('2026-10-20')
    assert blocked.status_code == 409
    assert blocked.json['error']['code'] == 'period_closed'
    assert change('2026-12-03').status_code == 200
    summary = client.get('/api/v1/dividend-summary?year=2026').json['data']
    assert [(row['month'], row['isa_gross_amount'], row['net_amount']) for row in summary] == [
        ('2026-12', 100, 90)]
