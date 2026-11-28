from unittest.mock import Mock
import pytest
from finance.schema import SCHEMA
from finance.store import GoogleSheets, Repository, operation


def test_google_adapter_raw_values_and_complete_headers():
    backend = GoogleSheets('test-id', 'unused.json')
    backend._book = Mock()
    backend._book.values_batch_get.return_value = {'valueRanges': [{'values': [headers]} for headers in SCHEMA.values()]}
    assert backend.read()['accounts'][0] == SCHEMA['accounts']
    ranges = backend._book.values_batch_get.call_args.args[0]
    assert ranges == [f"'{tab}'" for tab in SCHEMA]
    backend.apply([operation('accounts', 2, {'account_id': 'uuid', 'name': '=IMPORTXML("x")'})])
    payload = backend._book.values_batch_update.call_args.args[0]
    assert payload['valueInputOption'] == 'RAW'
    assert payload['data'][0]['range'] == "'accounts'!A2:I2"
    assert payload['data'][0]['values'][0][1].startswith('=IMPORTXML')


def test_journal_cannot_be_retargeted(tmp_path):
    Repository(GoogleSheets('sheet-one', 'unused'), tmp_path)
    with pytest.raises(RuntimeError, match='different spreadsheet'):
        Repository(GoogleSheets('sheet-two', 'unused'), tmp_path)


def test_pending_write_identifies_google_permission_denial(tmp_path, caplog):
    from gspread.exceptions import APIError
    from finance.schema import AppError
    from finance.store import MemorySheets

    backend = MemorySheets()
    response = Mock(status_code=403)
    response.json.return_value = {'error': {'code': 403, 'message': 'private response'}}
    backend.apply = Mock(side_effect=APIError(response))
    repo = Repository(backend, tmp_path)
    write = operation('settings', 2, {'key': 'display_name', 'value': 'private value'})
    with pytest.raises(AppError, match='편집자 권한') as failure:
        repo.mutate('request-403', {}, lambda raw, data: ([[write]], {}))
    assert failure.value.code == 'write_pending'
    assert 'request_id=request-403 http_status=403' in caplog.text
    assert 'private' not in caplog.text
    with repo.db() as db:
        assert db.execute('SELECT done FROM requests WHERE id=?', ('request-403',)).fetchone() == (0,)


def test_write_verifies_only_owned_columns(tmp_path):
    from finance.store import MemorySheets

    class SheetsWithExtraColumn(MemorySheets):
        def read(self):
            rows = super().read()
            if len(rows['settings']) > 1:
                rows['settings'][1].append('outside the app schema')
            return rows

    repo = Repository(SheetsWithExtraColumn(), tmp_path)
    write = operation('settings', 2, {'key': 'display_name', 'value': 'owner'})
    assert repo.mutate('request-200', {}, lambda raw, data: ([[write]], {'ok': True})) == {'ok': True}
