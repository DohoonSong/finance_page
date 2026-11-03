import functools
import time
from flask import Blueprint, current_app, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash
from .schema import AppError

bp = Blueprint('auth', __name__)


def login_required(fn):
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get('authenticated'):
            if request.path.startswith('/api/'):
                raise AppError('로그인이 필요합니다.', 401, 'unauthorized')
            return redirect(url_for('auth.login'))
        return fn(*args, **kwargs)
    return wrapped


@bp.route('/login', methods=['GET', 'POST'])
def login():
    error, status = None, 200
    if request.method == 'POST':
        repo = current_app.extensions['repository']
        with repo.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS login_attempts (at REAL)')
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM login_attempts WHERE at < ?', (time.time() - 900,))
            if db.execute('SELECT COUNT(*) FROM login_attempts').fetchone()[0] >= 10:
                error, status = '로그인 시도가 많습니다. 15분 후 다시 시도하세요.', 429
            else:
                db.execute('INSERT INTO login_attempts VALUES (?)', (time.time(),))
        if not error:
            valid = check_password_hash(current_app.config['ADMIN_PASSWORD_HASH'], request.form.get('password', ''))
            if request.form.get('username') == current_app.config['ADMIN_USERNAME'] and valid:
                session.clear()
                session['authenticated'] = True
                session.permanent = True
                with repo.db() as db:
                    db.execute('DELETE FROM login_attempts')
                return redirect(url_for('web.page', page='dashboard'))
            error, status = '아이디 또는 비밀번호를 확인하세요.', 401
    return render_template('login.html', error=error), status


@bp.post('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth.login'))
