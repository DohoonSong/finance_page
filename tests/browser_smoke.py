"""Optional Playwright smoke test. Only a temporary memory server is used.

Run with a Python environment containing playwright, after installing Chromium.
Uses this project's .venv for Flask. Screenshots go to docs/screenshots/.
"""
import os
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SERVER = '''
import tempfile
from finance import create_app
from werkzeug.security import generate_password_hash
app = create_app({'TESTING': True, 'DATA_BACKEND': 'memory', 'STATE_DIR': tempfile.mkdtemp(prefix='finance-browser-state-'), 'SECRET_KEY': 'browser-test-only-not-a-production-secret', 'ADMIN_USERNAME': 'owner', 'ADMIN_PASSWORD_HASH': generate_password_hash('browser-test-only'), 'WRITES_ENABLED': True, 'SESSION_COOKIE_SECURE': False})
app.run(host='127.0.0.1', port=5098, use_reloader=False)
'''


def main():
    environment = {**os.environ, 'APP_ENV': 'development', 'PYTHONPATH': str(ROOT)}
    process = subprocess.Popen([str(ROOT / '.venv/bin/python'), '-c', SERVER], cwd=ROOT, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                urlopen('http://127.0.0.1:5098/login', timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        screenshots = ROOT / 'docs/screenshots'
        screenshots.mkdir(exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            for width, height, name in [(1440, 1000, 'desktop'), (390, 844, 'mobile')]:
                page = browser.new_page(viewport={'width': width, 'height': height}, device_scale_factor=1)
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.goto('http://127.0.0.1:5098/login')
                page.locator('[name=username]').fill('owner')
                page.locator('[name=password]').fill('browser-test-only')
                page.get_by_role('button', name='로그인', exact=True).click()
                page.wait_for_url('**/dashboard')
                for route in ['dashboard', 'budgets', 'assets', 'investments', 'dividends', 'history', 'settings']:
                    page.goto('http://127.0.0.1:5098/' + route)
                    page.locator('#loading').wait_for(state='hidden')
                    assert page.locator('#workspace .panel').count() > 0, route
                    assert not page.locator('#message.error').is_visible(), page.locator('#message').inner_text()
                    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), f'{name}/{route}: overflow'
                    if route == 'dashboard':
                        page.screenshot(path=str(screenshots / f'{name}.png'), full_page=True)
                page.locator('[data-add=accounts]').click()
                page.locator('#editor [name=name]').fill('브라우저 확인용 ' + name)
                page.locator('#editor [name=type]').select_option('bank')
                page.locator('#editor-save').click()
                page.locator('#editor').wait_for(state='hidden')
                page.locator('#loading').wait_for(state='hidden')
                assert page.get_by_role('cell', name='브라우저 확인용 ' + name, exact=True).count() == 1
                page.goto('http://127.0.0.1:5098/budgets')
                page.locator('#loading').wait_for(state='hidden')
                page.locator('[data-add=budgets]').click()
                page.locator('#editor [name=category]').fill('테스트')
                page.locator('#editor [name=item]').fill('로컬 입력 확인 ' + name)
                page.locator('#editor [name=amount]').fill('100')
                page.locator('#editor [name=flow_type]').select_option('expense')
                page.locator('#editor-save').click()
                page.locator('#editor').wait_for(state='hidden')
                page.locator('#loading').wait_for(state='hidden')
                row = page.get_by_role('row').filter(has=page.get_by_role('cell', name='로컬 입력 확인 ' + name, exact=True))
                row.get_by_role('button', name='수정').click()
                page.locator('#editor [name=amount]').fill('200')
                page.locator('#editor-save').click()
                page.locator('#editor').wait_for(state='hidden')
                page.locator('#loading').wait_for(state='hidden')
                assert row.get_by_role('cell', name='200 원', exact=True).count() == 1
                if name == 'desktop':
                    page.goto('http://127.0.0.1:5098/history')
                    page.locator('#loading').wait_for(state='hidden')
                    page.locator('[data-action=preview]').click()
                    page.locator('[data-action=finalize]').click()
                    page.get_by_role('status').filter(has_text='월마감을 완료했습니다.').wait_for()
                    page.locator('#loading').wait_for(state='hidden')
                    page.on('dialog', lambda dialog: dialog.accept())
                    page.locator('[data-action=reopen]').click()
                    page.get_by_role('status').filter(has_text='새 리비전으로 재개했습니다.').wait_for()
                assert not errors, errors
                page.close()
            browser.close()
        print('PASS: 7 pages at 1440px and 390px; account create and budget create/edit; no JS errors or page overflow')
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == '__main__':
    main()
