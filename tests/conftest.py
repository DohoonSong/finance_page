import re
from uuid import uuid4
import pytest
from werkzeug.security import generate_password_hash
from finance import create_app
from finance.store import MemorySheets


@pytest.fixture
def backend():
    return MemorySheets()


@pytest.fixture
def app(tmp_path, backend):
    return create_app({'TESTING': True, 'SECRET_KEY': 'test-secret-key-with-more-than-32-characters', 'ADMIN_PASSWORD_HASH': generate_password_hash('correct-password'), 'STATE_DIR': str(tmp_path), 'WRITES_ENABLED': True, 'SESSION_COOKIE_SECURE': False, 'DATA_BACKEND': 'memory'}, backend)


@pytest.fixture
def client(app):
    client = app.test_client()
    html = client.get('/login').get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]+)"', html)[1]
    assert client.post('/login', data={'username': 'owner', 'password': 'correct-password', 'csrf_token': token}).status_code == 302
    return client


@pytest.fixture
def send(client):
    html = client.get('/dashboard').get_data(as_text=True)
    token = re.search(r'name="csrf-token" content="([^"]+)"', html)[1]
    def request(path, payload=None, method='POST', rid=None):
        return client.open('/api/v1' + path, method=method, json=payload if payload is not None else {}, headers={'X-CSRFToken': token, 'X-Request-ID': rid or str(uuid4())})
    return request
