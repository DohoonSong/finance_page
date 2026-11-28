import os
import re
import secrets
from datetime import timedelta
from pathlib import Path
from uuid import uuid4
import click
from dotenv import load_dotenv
from flask import Flask, g, jsonify, request, session
from flask_wtf.csrf import CSRFError, CSRFProtect
from werkzeug.exceptions import HTTPException
from werkzeug.security import generate_password_hash
from .schema import AppError
from .store import GoogleSheets, MemorySheets, Repository


def create_app(config=None, backend=None):
    load_dotenv()
    app = Flask(__name__)
    production = os.getenv('APP_ENV') == 'production'
    app.config.update(SECRET_KEY=os.getenv('SECRET_KEY'), ADMIN_USERNAME=os.getenv('ADMIN_USERNAME', 'owner'), ADMIN_PASSWORD_HASH=os.getenv('ADMIN_PASSWORD_HASH', ''), DATA_BACKEND=os.getenv('DATA_BACKEND', 'sheets'), SPREADSHEET_ID=os.getenv('SPREADSHEET_ID', '1MQ29rkoZKE02srltVNDd7Hs9i7igfEyPiAR5lOUelhs'), GOOGLE_APPLICATION_CREDENTIALS=os.getenv('GOOGLE_APPLICATION_CREDENTIALS', ''), STATE_DIR=os.getenv('STATE_DIR', str(Path(app.root_path).parent / 'instance')), CACHE_SECONDS=int(os.getenv('CACHE_SECONDS', '5')), WRITES_ENABLED=os.getenv('WRITES_ENABLED', 'false').lower() == 'true', SESSION_COOKIE_SECURE=os.getenv('SESSION_COOKIE_SECURE', str(production)).lower() == 'true', SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax', PERMANENT_SESSION_LIFETIME=timedelta(hours=8), MAX_CONTENT_LENGTH=64 * 1024, WTF_CSRF_TIME_LIMIT=28800)
    app.config.update(
        CLEV_BACKUP_FOLDER_ID=os.getenv('CLEV_BACKUP_FOLDER_ID', ''),
        DEPLOY_UPDATE_BRANCH=os.getenv('DEPLOY_UPDATE_BRANCH', ''),
        PYTHONANYWHERE_API_TOKEN=os.getenv('PYTHONANYWHERE_API_TOKEN', ''),
    )
    if config:
        app.config.update(config)
    if not app.config['SECRET_KEY']:
        if production:
            raise RuntimeError('Production requires SECRET_KEY')
        app.config['SECRET_KEY'] = secrets.token_hex(32)
    if production:
        failures = []
        if not isinstance(app.config['SECRET_KEY'], str) or len(app.config['SECRET_KEY']) < 32:
            failures.append('SECRET_KEY (minimum 32 characters)')
        if not app.config['ADMIN_PASSWORD_HASH']:
            failures.append('ADMIN_PASSWORD_HASH')
        if app.config['SESSION_COOKIE_SECURE'] is not True:
            failures.append('SESSION_COOKIE_SECURE=true')
        if app.config['DATA_BACKEND'] != 'sheets':
            failures.append('DATA_BACKEND=sheets')
        if failures:
            raise RuntimeError('Invalid production configuration: ' + ', '.join(failures))
    if app.config['DATA_BACKEND'] not in ('sheets', 'memory'):
        raise RuntimeError('Unknown DATA_BACKEND')
    if backend is None:
        backend = MemorySheets() if app.config['DATA_BACKEND'] == 'memory' else GoogleSheets(app.config['SPREADSHEET_ID'], app.config['GOOGLE_APPLICATION_CREDENTIALS'])
    state_dir = app.config['STATE_DIR']
    if isinstance(backend, MemorySheets) and not app.config.get('TESTING'):
        import tempfile
        state_dir = tempfile.mkdtemp(prefix='finance-demo-')
    app.extensions['repository'] = Repository(backend, state_dir, app.config['CACHE_SECONDS'])

    @app.before_request
    def request_context():
        supplied = request.headers.get('X-Request-ID', '')
        g.request_id = supplied if re.fullmatch(r'[A-Za-z0-9_-]{8,100}', supplied) else str(uuid4())
        if request.path.startswith('/api/') and not session.get('authenticated'):
            raise AppError('로그인이 필요합니다.', 401, 'unauthorized')

    CSRFProtect(app)
    from .auth import bp as auth_bp
    from .api import bp as api_bp
    from .web import bp as web_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(web_bp)
    from .deployment import bp as deployment_bp, capture_runtime
    app.extensions['deployment_runtime'] = capture_runtime()
    app.register_blueprint(deployment_bp)

    @app.errorhandler(AppError)
    def app_error(exc):
        return jsonify(error={'code': exc.code, 'message': str(exc)}, request_id=getattr(g, 'request_id', '')), exc.status

    @app.errorhandler(CSRFError)
    def csrf_error(exc):
        return jsonify(error={'code': 'csrf_failed', 'message': '보안 토큰이 만료되었습니다. 페이지를 새로고침하세요.'}, request_id=getattr(g, 'request_id', '')), 400

    @app.errorhandler(HTTPException)
    def http_error(exc):
        return jsonify(error={'code': exc.name, 'message': exc.description}, request_id=getattr(g, 'request_id', '')), exc.code

    @app.errorhandler(Exception)
    def unexpected(exc):
        app.logger.exception('Unhandled error request_id=%s', getattr(g, 'request_id', ''))
        return jsonify(error={'code': 'internal_error', 'message': '처리 중 오류가 발생했습니다. 요청 ID로 로그를 확인하세요.'}, request_id=getattr(g, 'request_id', '')), 500

    @app.after_request
    def headers(response):
        response.headers.update({'X-Request-ID': getattr(g, 'request_id', ''), 'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY', 'Referrer-Policy': 'same-origin', 'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'self'", 'Cache-Control': 'no-store'})
        if app.config['SESSION_COOKIE_SECURE']:
            response.headers['Strict-Transport-Security'] = 'max-age=31536000'
        if response.status_code == 429:
            response.headers['Retry-After'] = '900'
        return response

    @app.cli.command('password-hash')
    def password_hash():
        value = click.prompt('Password (12+ characters)', hide_input=True, confirmation_prompt=True)
        if len(value) < 12:
            raise click.ClickException('Use at least 12 characters')
        click.echo(generate_password_hash(value))

    @app.cli.command('check-schema')
    def check_schema():
        app.extensions['repository'].raw()
        click.echo('All 15 tab headers match. No data written.')

    from .formulas import register_cli
    register_cli(app)
    return app
