"""Owner-triggered fast-forward update for the PythonAnywhere deployment."""
import os
import re
import subprocess
from pathlib import Path

import requests
from filelock import FileLock, Timeout
from flask import Blueprint, current_app, jsonify, request

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


def update_branch():
    branch = current_app.config['DEPLOY_UPDATE_BRANCH']
    if current_app.config['PYTHONANYWHERE_API_TOKEN'] and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]*', branch):
        return branch
    return ''


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
    return git('rev-parse', '--short', 'HEAD')


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
                current_app.logger.warning('Git deployment update failed')
                raise AppError('Git 업데이트에 실패했습니다. 서버의 브랜치 상태와 GitHub 접근을 확인하세요.', 502, 'deploy_git_failed') from None
            try:
                response = requests.post(
                    RELOAD_URL,
                    headers={'Authorization': 'Token ' + current_app.config['PYTHONANYWHERE_API_TOKEN']},
                    timeout=15, allow_redirects=False,
                )
            except requests.RequestException:
                raise AppError(f'코드는 {commit}입니다. Reload 결과를 확인할 수 없습니다. PythonAnywhere Web 탭에서 확인하세요.', 502, 'deploy_reload_unknown') from None
            if not 200 <= response.status_code < 300:
                current_app.logger.warning('PythonAnywhere reload rejected status=%s', response.status_code)
                raise AppError(f'코드는 {commit}입니다. Reload 요청이 거부되었습니다. PythonAnywhere Web 탭에서 Reload할 수 있습니다.', 502, 'deploy_reload_failed')
    except Timeout:
        raise AppError('이미 업데이트가 진행 중입니다.', 409, 'deploy_busy') from None
    return jsonify(data={'commit': commit})
