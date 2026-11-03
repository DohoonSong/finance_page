"""Explicit operator-installed, spilling formulas; no production writes on startup."""
import json
import click
from .store import GoogleSheets


def formulas():
    budget = {'A2': '=IFERROR(SORT(UNIQUE(FILTER(budget_items!B2:B,budget_items!B2:B<>""))),"")'}
    for column, flow in (('B', 'income'), ('C', 'expense'), ('D', 'investment')):
        budget[column + '2'] = f'=MAP(A2:A,LAMBDA(m,IF(m="","",SUMIFS(budget_items!E2:E,budget_items!B2:B,m,budget_items!F2:F,"{flow}"))))'
    budget['E2'] = '=MAP(A2:A,B2:B,C2:C,D2:D,LAMBDA(m,i,e,v,IF(m="","",i-e-v-SUMIFS(budget_items!E2:E,budget_items!B2:B,m,budget_items!F2:F,"debt_principal"))))'
    portfolio = {
        'A2': '=IFERROR(FILTER(accounts!A2:A,((accounts!C2:C="brokerage")+(accounts!C2:C="retirement"))>0),"")',
    }
    # SORTN groups by account+symbol after sorting newest dates first. Taking
    # latest snapshots prevents summing the history of a position.
    latest = 'SORTN(SORT(FILTER({holdings!C2:C&"|"&holdings!D2:D,holdings!B2:B,holdings!C2:C,holdings!F2:F,holdings!G2:G},holdings!A2:A<>""),2,FALSE),9^9,2,1,TRUE)'
    for col, value_col in (('B', 4), ('C', 5)):
        portfolio[col + '2'] = f'=MAP(A2:A,LAMBDA(a,IF(a="","",IFERROR(LET(h,{latest},SUM(FILTER(INDEX(h,,{value_col}),INDEX(h,,3)=a))),0))))'
    portfolio['D2'] = '=ARRAYFORMULA(IF(A2:A="","",C2:C-B2:B))'
    portfolio['E2'] = '=MAP(A2:A,LAMBDA(a,IF(a="","",SUMIF(dividends!C2:C,a,dividends!G2:G))))'
    portfolio['F2'] = '=MAP(A2:A,LAMBDA(a,IF(a="","",SUMIF(realized_gains!C2:C,a,realized_gains!E2:E))))'
    portfolio['G2'] = '=ARRAYFORMULA(IF(A2:A="","",D2:D+E2:E+F2:F))'
    portfolio['H2'] = '=ARRAYFORMULA(IF(A2:A="","",IF(B2:B=0,"",G2:G/B2:B*100)))'
    return [{'range': f"'{tab}'!{cell}", 'values': [[formula]]} for tab, values in (('calc_budget', budget), ('calc_portfolio', portfolio)) for cell, formula in values.items()]


def register_cli(app):
    @app.cli.command('install-formulas')
    @click.option('--apply', is_flag=True, help='Write only to the two calc_ tabs; default prints a plan.')
    def install(apply):
        plan = formulas()
        if not apply:
            click.echo(json.dumps(plan, ensure_ascii=False, indent=2))
            return
        repo = app.extensions['repository']
        if not app.config['WRITES_ENABLED'] or not isinstance(repo.backend, GoogleSheets):
            raise click.ClickException('Requires Sheets backend and WRITES_ENABLED=true')
        with repo.locked():
            repo._recover()
            repo.raw()
            book = repo.backend.book
            existing = book.values_batch_get(["'calc_budget'!A2:E", "'calc_portfolio'!A2:H"], params={'valueRenderOption': 'FORMULA'})
            # Reinstallation is allowed only when anchors already match. Unknown
            # manual values/formulas must be reviewed; never clear an entire tab.
            for i, tab in enumerate(('calc_budget', 'calc_portfolio')):
                rows = existing['valueRanges'][i].get('values', [])
                anchors = [p['values'][0][0] for p in plan if p['range'].startswith(f"'{tab}'!")]
                if rows and rows[0] != anchors:
                    raise click.ClickException(f'{tab}: occupied or different formula anchors; review manually')
            book.values_batch_update({'valueInputOption': 'USER_ENTERED', 'data': plan})
            repo.cache = None
        click.echo('Formula anchors installed. Check both calc_ tabs for spill/locale errors.')
