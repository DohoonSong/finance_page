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
