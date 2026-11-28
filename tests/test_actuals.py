import sqlite3
from contextlib import closing
from unittest.mock import Mock

import pytest

from finance.actuals import HEADER, TAB, monthly_actuals, newest_backup, publish, read_backup
from finance.schema import AppError


def test_read_clevmoney_snapshot_with_signed_amounts_and_transfer(tmp_path):
    path = tmp_path / 'backup.db'
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript("""
            CREATE TABLE spendinglist (_id INTEGER PRIMARY KEY, s_date, s_price, s_cate, s_subcate, s_where, s_memo, s_card);
            CREATE TABLE earninglist (_id INTEGER PRIMARY KEY, e_date, e_price, e_cate, e_subcate, e_where, e_memo, e_card);
            CREATE TABLE catelist (_id INTEGER PRIMARY KEY, c_name);
            CREATE TABLE ecatelist (_id INTEGER PRIMARY KEY, ec_name);
            CREATE TABLE cardlist (_id INTEGER PRIMARY KEY, d_name);
            INSERT INTO catelist VALUES (1, '생활'), (2, '계좌 이체');
            INSERT INTO ecatelist VALUES (1, '급여');
            INSERT INTO cardlist VALUES (1, '현금');
            INSERT INTO spendinglist VALUES (1, '2026-11-02', '-300', '1', '0', '=2+2', '', '1'),
                (2, '2026-10-50', '100', '2', '0', '', '', '0');
            INSERT INTO earninglist VALUES (1, '2026-11-03', '500', '1', '0', '', '', '0');
        """)
    rows = read_backup(path)
    assert [row[:4] for row in rows] == [
        ['earninglist:1', '2026-11-03', 'income', 500],
        ['spendinglist:1', '2026-11-02', 'expense', -300],
        ['spendinglist:2', '2026-10-50', 'transfer', 100],
    ]
    assert rows[1][6:] == ['=2+2', '', '현금']


def test_latest_backup_skips_other_files_and_follows_next_page():
    drive = Mock()
    backup = {'name': 'clevmoney_261124_233950_auto.db', 'capabilities': {'canDownload': True}}
    drive.get.side_effect = [
        Mock(**{'json.return_value': {'files': [{'name': 'unrelated.db'}], 'nextPageToken': 'next'}}),
        Mock(**{'json.return_value': {'files': [backup]}}),
    ]
    assert newest_backup(drive, 'folder_id') == backup
    assert drive.get.call_args.kwargs['params']['pageToken'] == 'next'


def test_publish_is_idempotent_and_monthly_read_uses_index():
    book = Mock()
    book.fetch_sheet_metadata.return_value = {'sheets': [{'properties': {
        'title': TAB, 'gridProperties': {'rowCount': 100},
    }}]}
    book.values_get.return_value = {}
    source = {'id': 'backup1', 'modifiedTime': '2026-11-24T14:39:52Z', 'name': 'backup.db'}
    income = ['earninglist:1', '2026-11-04', 'income', 100, '', '', '', '', '']
    expense = ['spendinglist:1', '2026-11-01', 'expense', 30, '', '', '=2+2', '', '']
    transfer = ['spendinglist:2', '2026-10-11', 'transfer', 20, '', '', '', '', '']
    assert publish(book, source, [income, expense, transfer])['changed']
    payload = book.values_batch_update.call_args.args[0]
    assert payload['valueInputOption'] == 'RAW'
    marker = payload['data'][0]['values'][0]
    book.values_get.return_value = {'values': [marker, HEADER]}
    assert not publish(book, source, [])['changed']
    with pytest.raises(AppError, match='더 최신'):
        publish(book, {**source, 'modifiedTime': '2026-11-23T14:39:52Z'}, [])
    assert book.values_batch_update.call_count == 1
    book.values_get.side_effect = [{'values': [marker, HEADER]}, {'values': [income, expense]}]
    result = monthly_actuals(book, '2026-11')
    assert result['totals'] == {'income': 100, 'expense': 30, 'transfer': 0}
    assert book.values_get.call_args.args[0] == "'actual_transactions'!A3:I4"

    book.values_get.side_effect = None
    new_source = {**source, 'id': 'backup2', 'modifiedTime': '2026-11-25T14:39:52Z'}
    assert publish(book, new_source, [income])['count'] == 1
    assert book.values_batch_update.call_args.args[0]['data'][0]['values'][-2:] == [[''] * 9] * 2
