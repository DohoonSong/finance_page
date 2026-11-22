from subprocess import CalledProcessError
from unittest.mock import Mock

import pytest
import requests

from finance import deployment
from finance.schema import AppError


def test_deployment_requires_login_csrf_configuration_and_confirmation(app, client, send, monkeypatch):
    update = Mock()
    monkeypatch.setattr(deployment, 'fast_forward', update)
    path = '/deployment-update'
    assert app.test_client().post('/api/v1' + path).status_code == 401
    assert client.post('/api/v1' + path, json={'confirm': True}).status_code == 400
    assert send(path, {'confirm': True}).json['error']['code'] == 'deploy_disabled'
    app.config.update(DEPLOY_UPDATE_BRANCH='main', PYTHONANYWHERE_API_TOKEN='test-token')
    assert send(path, {'confirm': 'true'}).json['error']['code'] == 'deploy_unconfirmed'
    update.assert_not_called()


def test_deployment_reload_runs_after_git_and_keeps_token_private(app, client, send, monkeypatch):
    app.config.update(DEPLOY_UPDATE_BRANCH='main', PYTHONANYWHERE_API_TOKEN='test-token', WRITES_ENABLED=False)
    actions = Mock()
    actions.pull.return_value = 'abc1234'
    actions.reload.return_value = Mock(status_code=200)
    monkeypatch.setattr(deployment, 'fast_forward', actions.pull)
    monkeypatch.setattr(deployment.requests, 'post', actions.reload)
    result = send('/deployment-update', {'confirm': True, 'branch': 'ignored'})
    assert result.json['data'] == {'commit': 'abc1234'}
    assert [call[0] for call in actions.mock_calls] == ['pull', 'reload']
    actions.pull.assert_called_once_with('main')
    actions.reload.assert_called_once_with(
        deployment.RELOAD_URL, headers={'Authorization': 'Token test-token'},
        timeout=15, allow_redirects=False,
    )
    settings = client.get('/api/v1/settings')
    assert settings.json['data']['update_branch'] == 'main'
    assert b'test-token' not in result.data + settings.data

    actions.reload.reset_mock()
    actions.pull.side_effect = CalledProcessError(1, 'git', stderr='private output')
    result = send('/deployment-update', {'confirm': True})
    assert result.json['error']['code'] == 'deploy_git_failed'
    assert b'private output' not in result.data
    actions.reload.assert_not_called()

    actions.pull.side_effect = None
    actions.reload.side_effect = requests.Timeout('private output')
    assert send('/deployment-update', {'confirm': True}).json['error']['code'] == 'deploy_reload_unknown'


def test_fast_forward_checks_current_branch_and_clean_tree(monkeypatch):
    origin = 'https://github.com/DohoonSong/finance_page.git'
    git = Mock(side_effect=[origin, 'main', ' M README.md'])
    monkeypatch.setattr(deployment, 'git', git)
    with pytest.raises(AppError) as exc:
        deployment.fast_forward('main')
    assert exc.value.code == 'deploy_dirty'
    assert git.call_count == 3

    git.reset_mock()
    git.side_effect = [origin, 'main', '', '', '', '', 'abc1234']
    assert deployment.fast_forward('main') == 'abc1234'
    assert [call.args for call in git.call_args_list][3:] == [
        ('fetch', '--no-tags', 'origin', 'refs/heads/main'),
        ('merge-base', '--is-ancestor', 'HEAD', 'FETCH_HEAD'),
        ('merge', '--ff-only', 'FETCH_HEAD'),
        ('rev-parse', '--short', 'HEAD'),
    ]
