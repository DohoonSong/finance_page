"""Read a ClevMoney snapshot and publish actuals independently of budget rows."""
import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import AuthorizedSession
from google.oauth2.service_account import Credentials
from requests import RequestException

from .schema import AppError
from .services import date_value, month_value, now

TAB = 'actual_transactions'
RANGE = f"'{TAB}'!"
HEADER = ['source_id', 'date', 'flow_type', 'amount', 'category', 'subcategory', 'description', 'memo', 'payment_method']
DRIVE = 'https://www.googleapis.com/drive/v3/files'
NAME = re.compile(r'clevmoney_\d{6}_\d{6}(?:_auto)?\.db', re.IGNORECASE)
MAX_BACKUP_BYTES = 16 * 1024 * 1024


def newest_backup(session, folder):
    params = {
        'q': f"'{folder}' in parents and trashed = false and name contains 'clevmoney_'",
        'orderBy': 'modifiedTime desc', 'pageSize': 100,
        'fields': 'nextPageToken,files(id,name,modifiedTime,size,capabilities(canDownload))',
    }
    while True:
        response = session.get(DRIVE, params=params, timeout=20)
        response.raise_for_status()
        result = response.json()
        for item in result.get('files', []):
            if NAME.fullmatch(item['name']):
                if not item['capabilities']['canDownload']:
                    raise AppError('Drive 백업 파일을 다운로드할 수 없습니다. 공유 설정을 확인하세요.', 403, 'backup_download_denied')
                return item
        token = result.get('nextPageToken')
        if not token:
            raise AppError('Drive 폴더에서 ClevMoney 백업을 찾지 못했습니다.', 404, 'backup_not_found')
        params['pageToken'] = token


def read_backup(path):
    rows = []
    if path.stat().st_size > MAX_BACKUP_BYTES:
        raise AppError('백업 파일 크기 제한을 초과했습니다.', 422, 'backup_size')
    try:
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
            db.execute('PRAGMA trusted_schema=OFF')
            for table, prefix, flow, categories, cat in (
                ('spendinglist', 's', 'expense', 'catelist', 'c'),
                ('earninglist', 'e', 'income', 'ecatelist', 'ec'),
            ):
                # These identifiers are fixed to the inspected ClevMoney schema.
                query = f'''SELECT tx._id, tx.{prefix}_date, tx.{prefix}_price,
                    main.{cat}_name, sub.{cat}_name, tx.{prefix}_where, tx.{prefix}_memo, card.d_name
                    FROM {table} AS tx
                    LEFT JOIN {categories} AS main ON main._id = CAST(tx.{prefix}_cate AS INTEGER)
                    LEFT JOIN {categories} AS sub ON sub._id = CAST(tx.{prefix}_subcate AS INTEGER)
                    LEFT JOIN cardlist AS card ON card._id = CAST(tx.{prefix}_card AS INTEGER)'''
                for identifier, date, amount, category, subcategory, description, memo, card in db.execute(query):
                    if not re.fullmatch(r'-?[0-9]+', str(amount)):
                        raise ValueError('invalid amount')
                    amount = int(amount)
                    if abs(amount) >= 10**15:
                        raise ValueError('amount exceeds Sheets integer precision')
                    kind = 'transfer' if {category, subcategory} & {'이체', '계좌 이체', '계좌이체'} else flow
                    # ClevMoney uses day 50 in some historical rows. Keep the original
                    # date and include the transaction in its recorded month.
                    if re.fullmatch(r'\d{4}-\d{2}-50', date):
                        month_value(date[:7])
                    else:
                        date_value(date)
                    rows.append([f'{table}:{identifier}', date, kind, amount,
                                 category or '', subcategory or '', description, memo, card or ''])
    except (sqlite3.Error, ValueError, AppError):
        raise AppError('ClevMoney 백업의 구조 또는 거래 형식을 확인하세요.', 422, 'backup_schema') from None
    return sorted(rows, key=lambda row: (row[1], row[0]), reverse=True)


def download_latest(credentials_path, folder):
    try:
        credentials = Credentials.from_service_account_file(
            credentials_path, scopes=['https://www.googleapis.com/auth/drive.readonly'],
        )
        with AuthorizedSession(credentials) as session:
            item = newest_backup(session, folder)
            if int(item.get('size', 0)) > MAX_BACKUP_BYTES:
                raise AppError('백업 파일 크기 제한을 초과했습니다.', 422, 'backup_size')
            with TemporaryDirectory(prefix='finance-backup-') as directory:
                path = Path(directory) / 'backup.db'
                with session.get(f"{DRIVE}/{item['id']}", params={'alt': 'media'}, stream=True, timeout=30) as response:
                    response.raise_for_status()
                    written = 0
                    with path.open('wb') as output:
                        for chunk in response.iter_content(65536):
                            written += len(chunk)
                            if written > MAX_BACKUP_BYTES:
                                raise AppError('백업 파일 크기 제한을 초과했습니다.', 422, 'backup_size')
                            output.write(chunk)
                return item, read_backup(path)
    except (GoogleAuthError, RequestException, OSError, ValueError):
        raise AppError('Drive 백업을 읽지 못했습니다. Drive API와 서비스 계정의 폴더 뷰어 권한을 확인하세요.', 503, 'backup_drive') from None


def actual_tab(book):
    metadata = book.fetch_sheet_metadata(params={'fields': 'sheets(properties(title,gridProperties(rowCount)))'})
    return next((sheet['properties'] for sheet in metadata['sheets'] if sheet['properties']['title'] == TAB), None)


def actual_state(book, tab):
    if not tab:
        return []
    rows = book.values_get(RANGE + '1:2', params={'valueRenderOption': 'UNFORMATTED_VALUE'}).get('values', [])
    if rows and (len(rows) != 2 or rows[1] != HEADER or len(rows[0]) != 6
                 or type(rows[0][4]) is not int or rows[0][4] < 0):
        raise AppError('실제 거래 탭의 헤더를 확인하세요.', 503, 'actual_schema')
    return rows[0] if rows else []


def publish(book, item, rows):
    tab = actual_tab(book)
    previous = actual_state(book, tab)
    if previous and (previous[0], previous[1]) == (item['id'], item['modifiedTime']):
        return {'filename': previous[2], 'count': previous[4], 'changed': False}
    if previous and datetime.fromisoformat(previous[1]) > datetime.fromisoformat(item['modifiedTime']):
        raise AppError('이미 더 최신 백업이 반영되어 있습니다.', 409, 'older_backup')
    ranges = {}
    for index, row in enumerate(rows, 3):
        month = row[1][:7]
        bounds = ranges.setdefault(month, [index, index])
        bounds[1] = index
    marker = [item['id'], item['modifiedTime'], item['name'], now(), len(rows), json.dumps(ranges)]
    clear_count = max(0, (int(previous[4]) if previous else 0) - len(rows))
    values = [marker, HEADER, *rows, *([[''] * len(HEADER)] * clear_count)]
    if len(json.dumps(values).encode()) > 8_000_000:
        raise AppError('거래 스냅샷 크기 제한을 초과했습니다.', 422, 'actual_size')
    if not tab:
        book.add_worksheet(TAB, rows=max(100, len(values)), cols=len(HEADER))
    elif tab['gridProperties']['rowCount'] < len(values):
        book.worksheet(TAB).resize(rows=len(values))
    # In one Sheets request, replace the snapshot, clear removed rows and record the source.
    book.values_batch_update({'valueInputOption': 'RAW', 'data': [{'range': RANGE + 'A1', 'values': values}]})
    return {'filename': item['name'], 'count': len(rows), 'changed': True}


def monthly_actuals(book, month):
    state = actual_state(book, actual_tab(book))
    rows = []
    if state:
        try:
            bounds = json.loads(state[5]).get(month)
        except (ValueError, TypeError, AttributeError):
            raise AppError('실제 거래 탭의 월별 행 범위를 확인하세요.', 503, 'actual_schema') from None
        if bounds:
            if (not isinstance(bounds, list) or len(bounds) != 2
                    or any(type(index) is not int for index in bounds)
                    or not 3 <= bounds[0] <= bounds[1] <= state[4] + 2):
                raise AppError('실제 거래 탭의 월별 행 범위를 확인하세요.', 503, 'actual_schema')
            start, end = bounds
            values = book.values_get(RANGE + f'A{start}:I{end}', params={'valueRenderOption': 'UNFORMATTED_VALUE'}).get('values', [])
            rows = [dict(zip(HEADER, row + [''] * (len(HEADER) - len(row)))) for row in values]
    totals = {flow: sum(row['amount'] for row in rows if row['flow_type'] == flow) for flow in ('income', 'expense', 'transfer')}
    return {'month': month, 'items': rows, 'totals': totals, 'filename': state[2] if state else ''}
