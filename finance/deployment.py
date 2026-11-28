"""Owner-triggered fast-forward update for the PythonAnywhere deployment."""
import json
import os
import re
import subprocess
import time
from pathlib import Path

import requests
from filelock import FileLock, Timeout
from flask import Blueprint, current_app, g, jsonify, request

from .auth import login_required
from .schema import AppError

bp = Blueprint('deployment', __name__, url_prefix='/api/v1')
PROJECT = Path(__file__).resolve().parents[1]
ORIGINS = {
    'https://github.com/DohoonSong/finance_page.git',
    'https://github.com/DohoonSong/finance_page',
    'git@github.com:DohoonSong/finance_page.git',
}
RELOAD_URL = 'https://www.pythonanywhere.com/api/v0/user/cafedoroon/webapps/cafedoroon.pythonanywhere.com/reload/'


def deployment_config():
    branch = current_app.config['DEPLOY_UPDATE_BRANCH']
    token = current_app.config['PYTHONANYWHERE_API_TOKEN']
    missing = []
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]*', branch):
        missing.append('DEPLOY_UPDATE_BRANCH')
    if not token.strip():
        missing.append('PYTHONANYWHERE_API_TOKEN')
    return {'branch': branch if not missing else '', 'missing': missing}


def update_branch():
    return deployment_config()['branch']


def capture_runtime():
    try:
        commit = git('rev-parse', 'HEAD')
    except (OSError, subprocess.SubprocessError):
        commit = ''
    return {'commit': commit, 'started_at': time.time()}


def deployment_state_path():
    return Path(current_app.extensions['repository'].db_path).parent / 'deployment.json'


def save_attempt(attempt):
    path = deployment_state_path()
    pending = path.with_suffix('.tmp')
    pending.write_text(json.dumps(attempt), encoding='utf-8')
    pending.replace(path)


@bp.get('/deployment-status')
@login_required
def status():
    try:
        attempt = json.loads(deployment_state_path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        attempt = {}
    runtime = current_app.extensions['deployment_runtime']
    if not isinstance(attempt, dict) or attempt.get('request_id') != request.args.get('request_id'):
        attempt = {}
    target = attempt.get('commit')
    confirmed = bool(target and target == runtime['commit']
                     and runtime['started_at'] >= attempt.get('requested_at', float('inf')))
    return jsonify(data={'commit': runtime['commit'], 'target': target, 'confirmed': confirmed})


def git(*args):
    env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0'}
    env.pop('PYTHONANYWHERE_API_TOKEN', None)
    return subprocess.run(
        ['git', '-c', 'core.hooksPath=/dev/null', *args],
        cwd=PROJECT, env=env, stdin=subprocess.DEVNULL, capture_output=True,
        text=True, check=True, timeout=45,
    ).stdout.rstrip('\n')


def fast_forward(branch):
    if git('remote', 'get-url', 'origin') not in ORIGINS:
        raise AppError('업데이트 원격 저장소가 설정과 다릅니다.', 409, 'deploy_origin')
    if git('branch', '--show-current') != branch:
        raise AppError('서버의 현재 브랜치가 업데이트 설정과 다릅니다.', 409, 'deploy_branch')
    if git('status', '--porcelain'):
        raise AppError('서버 작업 폴더에 커밋되지 않은 변경이 있습니다.', 409, 'deploy_dirty')
    git('fetch', '--no-tags', 'origin', 'refs/heads/' + branch)
    git('merge-base', '--is-ancestor', 'HEAD', 'FETCH_HEAD')
    git('merge', '--ff-only', 'FETCH_HEAD')
    return git('rev-parse', 'HEAD')


@bp.post('/deployment-update')
@login_required
def update():
    branch = update_branch()
    if not branch:
        raise AppError('서버 업데이트 기능이 설정되지 않았습니다.', 403, 'deploy_disabled')
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or data.get('confirm') is not True:
        raise AppError('업데이트 및 Reload를 확인해 주세요.', 400, 'deploy_unconfirmed')

    state_dir = Path(current_app.extensions['repository'].db_path).parent
    try:
        with FileLock(state_dir / 'deployment.lock', timeout=0):
            try:
                commit = fast_forward(branch)
            except (OSError, subprocess.SubprocessError):
                current_app.logger.warning('Git deployment update failed request_id=%s', g.request_id)
                raise AppError('Git 업데이트에 실패했습니다. 서버의 브랜치 상태와 GitHub 접근을 확인하세요.', 502, 'deploy_git_failed') from None
            attempt = {'request_id': g.request_id, 'commit': commit, 'requested_at': time.time()}
            try:
                save_attempt(attempt)
            except OSError:
                raise AppError('코드는 가져왔지만 업데이트 상태를 기록하지 못했습니다. Web 탭에서 Reload하세요.', 502, 'deploy_state_failed') from None
            current_app.logger.info('Git deployment updated request_id=%s commit=%s', g.request_id, commit)
            try:
                response = requests.post(
                    RELOAD_URL,
                    headers={'Authorization': 'Token ' + current_app.config['PYTHONANYWHERE_API_TOKEN']},
                    timeout=15, allow_redirects=False,
                )
            except requests.RequestException:
                raise AppError(f'코드는 {commit[:7]}입니다. PythonAnywhere API의 응답을 받지 못했습니다.', 502, 'deploy_reload_unknown') from None
            if not 200 <= response.status_code < 300:
                status_code = response.status_code
                current_app.logger.warning('PythonAnywhere reload rejected request_id=%s status=%s', g.request_id, status_code)
                hint = ('API 토큰과 웹앱 관리 권한을 확인하세요.' if status_code in (401, 403)
                        else 'PythonAnywhere Web 탭의 상태를 확인하세요.')
                raise AppError(f'코드는 {commit[:7]}입니다. Reload 요청이 거부되었습니다 (HTTP {status_code}). {hint}', 502, 'deploy_reload_failed')
            current_app.logger.info('PythonAnywhere reload accepted request_id=%s status=%s', g.request_id, response.status_code)
    except Timeout:
        raise AppError('이미 업데이트가 진행 중입니다.', 409, 'deploy_busy') from None
    return jsonify(data={'commit': commit, 'request_id': g.request_id})
