import hashlib
import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import Blueprint, current_app, g, jsonify, request
from .auth import login_required
from .deployment import update_branch
from .schema import AppError
from .services import (CATEGORY_TABLES, TOSS_CATEGORIES, assets_summary, budget_rows, budget_summary, close_preview, copy_budget, date_value, dividend_summary, effective_closings, finalize, investment_rows, month_end, month_value, now, portfolio_summary, reopen, visible_settings, write_record)
from .store import operation

bp = Blueprint('api', __name__, url_prefix='/api/v1')
RESOURCES = {'accounts': 'accounts', 'asset-items': 'asset_items', 'budgets': 'budget_items', 'recurring-items': 'recurring_items', 'asset-snapshots': 'asset_snapshots', 'liabilities': 'liabilities', 'liability-snapshots': 'liability_snapshots', 'holdings': 'holdings', 'dividends': 'dividends', 'realized-gains': 'realized_gains'}


@bp.before_request
@login_required
def protect():
    if request.method in ('POST', 'PATCH', 'DELETE'):
        if not current_app.config['WRITES_ENABLED']:
            raise AppError('현재 읽기 전용입니다. 운영 설정에서 쓰기를 활성화하세요.', 403, 'read_only')
        if not re.fullmatch(r'[A-Za-z0-9_-]{8,100}', request.headers.get('X-Request-ID', '')):
            raise AppError('8~100자 X-Request-ID 헤더가 필요합니다.', 400)


def repository():
    return current_app.extensions['repository']


def body(allowed=None):
    if not request.is_json:
        raise AppError('JSON 본문이 필요합니다.', 400)
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise AppError('JSON 객체가 필요합니다.', 400)
    if allowed is not None and set(value) - set(allowed):
        raise AppError('지원하지 않는 필드가 있습니다.', 422)
    return value


def ok(data, status=200):
    return jsonify(data=data, request_id=g.request_id), status


def selected_month():
    return month_value(request.args.get('month', datetime.now(ZoneInfo('Asia/Seoul')).strftime('%Y-%m')))


def selected_year():
    value = request.args.get('year', datetime.now(ZoneInfo('Asia/Seoul')).strftime('%Y'))
    if not re.fullmatch(r'[1-9]\d{3}', value):
        raise AppError('연도는 YYYY 형식이어야 합니다.', 400)
    return value


@bp.route('/<resource>', methods=['GET', 'POST'])
@bp.route('/<resource>/<identifier>', methods=['PATCH', 'DELETE'])
def records(resource, identifier=None):
    table = RESOURCES.get(resource)
    if not table:
        raise AppError('경로를 찾을 수 없습니다.', 404)
    if request.method == 'GET':
        data = repository().read()
        rows = investment_rows(data, table) if table in CATEGORY_TABLES else data[table]
        if resource == 'budgets':
            rows = budget_rows(data, selected_month())
        if resource == 'holdings' and request.args.get('account_id'):
            rows = [r for r in rows if r['account_id'] == request.args['account_id']]
        if resource == 'dividends':
            year = selected_year()
            rows = sorted((r for r in rows if r['payment_date'][:4] == year),
                          key=lambda row: row['payment_date'], reverse=True)
        return ok(rows)
    if request.method == 'DELETE' and resource != 'budgets':
        raise AppError('이 항목은 삭제할 수 없습니다.', 405)
    value = body()
    version = value.pop('expected_version', None)
    return ok(write_record(repository(), g.request_id, table, value, request.method, identifier, version), 201 if request.method == 'POST' else 200)


@bp.get('/dashboard')
def dashboard():
    month = selected_month()
    data = repository().read()
    return ok({'month': month, 'assets': assets_summary(data, month_end(month)), 'budget': budget_summary(data, month), 'portfolio': portfolio_summary(data, month_end(month)), 'history': [r for r in effective_closings(data) if r['status'] == 'closed']})


@bp.get('/budget-summary')
def budgets():
    return ok(budget_summary(repository().read(), selected_month(), request.args.get('category')))


@bp.post('/budgets/<month>/copy')
def copy(month):
    value = body(('source_month', 'expected_version'))
    return ok(copy_budget(repository(), g.request_id, month_value(month), value.get('source_month'), value.get('expected_version')), 201)


@bp.get('/assets')
def assets():
    cutoff = date_value(request.args.get('date', datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat()))
    return ok(assets_summary(repository().read(), cutoff))


def investment_filters(*, allow_portfolio_groups=False):
    account_id = request.args.get('account_id')
    category = request.args.get('investment_category')
    if category and (not account_id or len(category) > 200 or (not allow_portfolio_groups and category not in TOSS_CATEGORIES)):
        raise AppError('계좌와 유효한 투자 분류를 선택하세요.', 400)
    return {'account_id': account_id, 'category': category}


@bp.get('/portfolio-summary')
def portfolio():
    return ok(portfolio_summary(repository().read(), **investment_filters(allow_portfolio_groups=True)))


@bp.get('/dividend-summary')
def dividends():
    year = selected_year()
    month = request.args.get('month')
    if month and month_value(month)[:4] != year:
        raise AppError('지급 월이 조회 연도와 일치하지 않습니다.', 400)
    return ok(dividend_summary(repository().read(), year, month=month,
                               symbol=request.args.get('symbol'), **investment_filters()))


@bp.get('/closings')
@bp.get('/closings/<month>')
def closings(month=None):
    data = repository().read()
    if month:
        month_value(month)
        rows = [r for r in data['monthly_closings'] if r['month'] == month]
        ids = {r['closing_id'] for r in rows}
        return ok({'items': rows, 'details': [r for r in data['closing_details'] if r['closing_id'] in ids]})
    return ok(data['monthly_closings'])


@bp.post('/closings/<month>/preview')
def preview(month):
    body(('expected_version',))
    return ok(close_preview(repository().read(fresh=True), month_value(month)))


@bp.post('/closings/<month>/finalize')
def close(month):
    return ok(finalize(repository(), g.request_id, month_value(month), body(('expected_version', 'data_hash'))))


@bp.post('/closings/<month>/reopen')
def reopen_month(month):
    return ok(reopen(repository(), g.request_id, month_value(month), body(('expected_version',))))


@bp.get('/net-worth/history')
def history():
    return ok(sorted([r for r in effective_closings(repository().read()) if r['status'] == 'closed'], key=lambda r: r['month']))


def settings_version(rows):
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


@bp.route('/settings', methods=['GET', 'PATCH'])
def settings():
    if request.method == 'GET':
        rows = visible_settings(repository().read())
        return ok({'items': rows, 'version': settings_version(rows), 'writes_enabled': current_app.config['WRITES_ENABLED'], 'backend': current_app.config['DATA_BACKEND'], 'timezone': 'Asia/Seoul', 'base_currency': 'KRW', 'update_branch': update_branch()})
    value = body(('expected_version', 'display_name'))
    name = value.get('display_name')
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
        raise AppError('표시 이름은 1~100자입니다.')
    def plan(raw, data):
        if value.get('expected_version') != settings_version(visible_settings(data)):
            raise AppError('설정이 변경되었습니다. 새로고침하세요.', 409)
        row = next((i for i, r in enumerate(raw['settings'][1:], 2) if r and r[0] == 'display_name'), len(raw['settings']) + 1)
        record = dict(key='display_name', value=name.strip(), value_type='string', description='웹앱 표시 이름', updated_at=now())
        return [[operation('settings', row, record)]], record
    return ok(repository().mutate(g.request_id, ['settings', value], plan))


@bp.get('/backup')
def backup():
    response = jsonify({'schema_version': 2, 'exported_at': now(), 'tables': repository().read(fresh=True)})
    response.headers['Content-Disposition'] = 'attachment; filename=finance-backup.json'
    return response
