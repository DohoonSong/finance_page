"""Sheets is the financial source of truth; SQLite is only a durable write journal.

One deployment, one shared lock/journal, no out-of-band sheet edits. Absolute cell
writes can be replayed after ambiguous timeouts without duplicate append rows.
"""
import copy
import hashlib
import json
import logging
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock, Timeout
from gspread.exceptions import APIError

from .schema import SCHEMA, AppError

logger = logging.getLogger(__name__)


def column(n):
    result = ''
    while n:
        n, r = divmod(n - 1, 26)
        result = chr(65 + r) + result
    return result


class MemorySheets:
    """Empty, isolated local demo/test backend. Never connects to Google."""
    def __init__(self):
        self.cells = {k: [v.copy()] for k, v in SCHEMA.items()}

    def read(self):
        return copy.deepcopy(self.cells)

    def apply(self, operations):
        for op in operations:
            tab, row, values = op['tab'], op['row'], op['values']
            while len(self.cells[tab]) < row:
                self.cells[tab].append([])
            self.cells[tab][row - 1] = values.copy()


class GoogleSheets:
    def __init__(self, spreadsheet_id, credentials):
        self.spreadsheet_id, self.credentials = spreadsheet_id, credentials
        self._book = None

    @property
    def book(self):
        if self._book is None:
            import gspread
            client = gspread.service_account(filename=self.credentials, scopes=['https://www.googleapis.com/auth/spreadsheets'])
            client.set_timeout(30)
            self._book = client.open_by_key(self.spreadsheet_id)
        return self._book

    def read(self):
        result = self.book.values_batch_get([f"'{tab}'" for tab in SCHEMA], params={'valueRenderOption': 'UNFORMATTED_VALUE'})
        return {tab: result['valueRanges'][i].get('values', []) for i, tab in enumerate(SCHEMA)}

    def apply(self, operations):
        if not operations:
            return
        self.book.values_batch_update({'valueInputOption': 'RAW', 'data': [
            {'range': f"'{op['tab']}'!A{op['row']}:{column(len(SCHEMA[op['tab']]))}{op['row']}", 'values': [op['values']]}
            for op in operations
        ]})


class Repository:
    def __init__(self, backend, state_dir, cache_seconds=5):
        self.backend, self.cache_seconds = backend, cache_seconds
        Path(state_dir).mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = FileLock(str(Path(state_dir) / 'writes.lock'), timeout=15)
        self.db_path = str(Path(state_dir) / 'journal.sqlite3')
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)')
            scope = 'sheets:' + backend.spreadsheet_id if isinstance(backend, GoogleSheets) else 'memory'
            prior = db.execute("SELECT value FROM metadata WHERE key='store'").fetchone()
            if prior and prior[0] != scope:
                raise RuntimeError('STATE_DIR belongs to a different spreadsheet/backend')
            db.execute("INSERT OR IGNORE INTO metadata VALUES ('store', ?)", (scope,))
            db.execute('CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, fingerprint TEXT, plan TEXT, result TEXT, done INTEGER DEFAULT 0)')
        Path(self.db_path).chmod(0o600)
        self.cache = None

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.db_path, timeout=20)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @contextmanager
    def locked(self):
        try:
            with self.lock:
                yield
        except Timeout as exc:
            raise AppError('저장소가 사용 중입니다. 같은 request-id로 재시도하세요.', 503, 'store_busy') from exc

    def raw(self):
        try:
            raw = self.backend.read()
        except Exception as exc:
            raise AppError('Google Sheets 연결을 확인하세요.', 503, 'store_unavailable') from exc
        for tab, fields in SCHEMA.items():
            if not raw.get(tab) or raw[tab][0] != fields:
                raise AppError(f'{tab}: 헤더가 인계 문서의 스키마와 다릅니다.', 503, 'schema_mismatch')
        return raw

    @staticmethod
    def records(raw):
        return {tab: [dict(zip(SCHEMA[tab], row + [''] * (len(SCHEMA[tab]) - len(row)))) for row in rows[1:] if row and row[0] != ''] for tab, rows in raw.items()}

    def _recover(self):
        with self.db() as db:
            pending = db.execute('SELECT id, plan FROM requests WHERE done=0 ORDER BY rowid').fetchall()
        for rid, plan in pending:
            self.raw()  # Never replay into an incompatible schema.
            for operations in json.loads(plan):
                try:
                    self.backend.apply(operations)
                except APIError as exc:
                    status = exc.response.status_code
                    logger.warning('Sheets write rejected request_id=%s http_status=%s', rid, status)
                    if status == 403:
                        self._write_pending('Google Sheets에서 쓰기를 거부했습니다. 서비스 계정의 편집자 권한과 시트 보호 설정을 확인하세요.')
                    if status == 400:
                        self._write_pending('Google Sheets에서 저장 범위 또는 입력값을 거부했습니다. 서버 로그에서 요청 ID를 확인하세요.')
                    self._write_pending()
                except Exception:
                    logger.warning('Sheets write failed request_id=%s', rid)
                    self._write_pending()
                try:
                    raw = self.raw()
                except AppError as exc:
                    logger.warning('Sheets read-back failed request_id=%s code=%s', rid, exc.code)
                    self._write_pending('저장 후 Google Sheets를 다시 읽지 못했습니다. 연결과 시트 헤더를 확인하세요.')
                for op in operations:
                    tab, row, expected = op['tab'], op['row'], op['values']
                    actual = raw[tab][row - 1][:len(expected)] if len(raw[tab]) >= row else []
                    actual += [''] * (len(expected) - len(actual))
                    if actual != expected:
                        logger.warning('Sheets read-back mismatch request_id=%s tab=%s row=%s', rid, tab, row)
                        self._write_pending('Google Sheets의 저장값이 요청과 일치하지 않습니다. 서버 로그에서 요청 ID를 확인하세요.')
            with self.db() as db:
                db.execute('UPDATE requests SET done=1 WHERE id=?', (rid,))
            self.cache = None

    def _write_pending(self, message='저장 확인에 실패했습니다. 같은 request-id로 재시도하면 복구합니다.'):
        self.cache = None
        raise AppError(message, 503, 'write_pending') from None

    def read(self, fresh=False):
        with self.locked():
            self._recover()
            with self.db() as db:
                generation = db.execute('SELECT COUNT(*) FROM requests WHERE done=1').fetchone()[0]
            if not fresh and self.cache and self.cache[0] == generation and time.monotonic() - self.cache[1] < self.cache_seconds:
                return copy.deepcopy(self.cache[2])
            records = self.records(self.raw())
            self.cache = (generation, time.monotonic(), records)
            return copy.deepcopy(records)

    def mutate(self, request_id, payload, planner):
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        with self.locked():
            self._recover()
            with self.db() as db:
                previous = db.execute('SELECT fingerprint, result FROM requests WHERE id=?', (request_id,)).fetchone()
            if previous:
                if previous[0] != fingerprint:
                    raise AppError('request-id가 다른 요청에 이미 사용되었습니다.', 409, 'idempotency_conflict')
                return json.loads(previous[1])
            raw = self.raw()
            plan, result = planner(raw, self.records(raw))
            with self.db() as db:
                db.execute('INSERT INTO requests(id,fingerprint,plan,result) VALUES (?,?,?,?)', (request_id, fingerprint, json.dumps(plan), json.dumps(result)))
            self._recover()
            return result


def operation(tab, row, record):
    return {'tab': tab, 'row': row, 'values': [record.get(k, '') for k in SCHEMA[tab]]}


def row_for(raw, tab, identifier):
    for index, row in enumerate(raw[tab][1:], 2):
        if row and row[0] == identifier:
            return index
    raise AppError('항목을 찾을 수 없습니다.', 404, 'not_found')
