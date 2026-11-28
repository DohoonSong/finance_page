from flask import Blueprint, abort, current_app, redirect, render_template, url_for
from .auth import login_required
from .services import DIVIDEND_GROUPS, TOSS_CATEGORIES

bp = Blueprint('web', __name__)
PAGES = {'dashboard': '대시보드', 'budgets': '월별 예산', 'actuals': '실제 수입·지출', 'assets': '자산과 부채', 'investments': '투자', 'dividends': '배당', 'history': '이력과 월마감', 'settings': '설정'}


@bp.get('/')
def index():
    return redirect(url_for('web.page', page='dashboard'))


@bp.get('/<page>')
@login_required
def page(page):
    if page not in PAGES:
        abort(404)
    return render_template('app.html', page=page, pages=PAGES, title=PAGES[page], demo=current_app.config['DATA_BACKEND'] == 'memory', toss_categories=TOSS_CATEGORIES, dividend_groups=DIVIDEND_GROUPS, actuals_enabled=bool(current_app.config['CLEV_BACKUP_FOLDER_ID']) and current_app.config['DATA_BACKEND']=='sheets', writes=current_app.config['WRITES_ENABLED'])
