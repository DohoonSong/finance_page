# Personal Finance Web — Codex Handoff (v2.0)

## Project objective
Build a private responsive financial-management web application deployed on PythonAnywhere, backed by **a newly created native Google Sheet**. **Do not edit** the original `가계부.xlsx` or ClevMoney backups. The original workbook is only a reference for business logic; the new spreadsheet is the operational datastore. No financial records were copied over.

## Exact data source
- Native Google Sheet: https://docs.google.com/spreadsheets/d/1MQ29rkoZKE02srltVNDd7Hs9i7igfEyPiAR5lOUelhs/edit
- Spreadsheet ID: `1MQ29rkoZKE02srltVNDd7Hs9i7igfEyPiAR5lOUelhs`
- Document title: `Personal Finance Web DB v2`
- 15 tabs have been created and verified. Row 1 contains field headers; tab `settings` has six system values. All other rows are **empty**. The `calc_` tabs currently contain headers only; no formulas yet. Codex should add computation formulas as part of implementation.
- A service account must be separately provisioned in Google Cloud, Sheets API enabled, and the spreadsheet shared with the service-account email. The ChatGPT Drive connector connection does not automatically authorize PythonAnywhere.

## Sheets schema (exact column order)
| Tab | Fields |
|---|---|
| accounts | account_id, name, type, institution, currency, active, created_at, updated_at, version |
| asset_items | asset_id, account_id, category, name, active, created_at, updated_at, version |
| budget_items | budget_id, month, category, item, amount, flow_type, note, created_at, updated_at, version |
| recurring_items | recurring_id, item, category, flow_type, day, amount, start_month, end_month, active, updated_at, version |
| asset_snapshots | snapshot_id, date, asset_id, amount, note, created_at, updated_at, version |
| liabilities | loan_id, name, original_principal, annual_rate, start_date, maturity_date, active, updated_at, version |
| liability_snapshots | snapshot_id, date, loan_id, balance, created_at, updated_at, version |
| holdings | holding_id, date, account_id, symbol, name, cost_basis, market_value, currency, updated_at, version |
| dividends | dividend_id, payment_date, account_id, symbol, gross_amount, tax_amount, net_amount, currency, note, created_at, updated_at, version |
| realized_gains | gain_id, trade_date, account_id, symbol, profit, currency, note, created_at, updated_at, version |
| monthly_closings | closing_id, month, status, revision, assets_total, liabilities_total, net_worth, budget_balance, portfolio_profit, data_hash, closed_at, created_at |
| closing_details | closing_id, revision, item_type, item_id, amount, as_of_date, source_ref |
| calc_budget | month, income_total, expense_total, investment_allocation, budget_balance |
| calc_portfolio | account_id, cost_basis, market_value, unrealized_profit, dividend_total, realized_profit, total_profit, simple_return_pct |
| settings | key, value, value_type, description, updated_at |

## Data conventions
- ISO date `YYYY-MM-DD`; month `YYYY-MM`; timestamps UTC ISO 8601 (display Seoul local time).
- All input money values in integer units of `currency`; report base currency `KRW`. Never sum USD and KRW without explicit FX normalization. Rate `annual_rate` decimal fraction (e.g. 4.012% = 0.04012).
- `flow_type` explicit taxonomy (`income`, `expense`, `investment`, `transfer`, `debt_principal`) to prevent investment and principal repayments from being treated as consumption.
- UUID for identifiers; never identify a record only by physical row number. `version` optimistic concurrency check; beware Sheets lacks atomic conditional update: require serialized writes and conflict detection.
- Asset category design must avoid double counting brokerage/retirement positions and aggregate account balances. Map holdings into net-worth calculations once.
- Dividend cash flow should not double-count against aggregate asset snapshots; gross/tax/net differentiation is mandatory.
- Closed periods are immutable except reopening as new revision; `monthly_closings` status flow `open -> preparing -> closed`, idempotent month+revision+request-id. Persist `closing_details`, verify, then set status `closed`. Recovery on partial failure.
- `calc_budget` and `calc_portfolio` are derived views, not user-editable input tables; Google Sheets formulas for basic aggregation, Python for multi-period and closing logic. Add formula cells only once the input taxonomy is finalized.

## Flask REST API `/api/v1`
- GET `/dashboard?month=YYYY-MM`
- GET `/accounts`, POST `/accounts`, PATCH `/accounts/{id}`
- GET `/budgets?month=YYYY-MM`, POST `/budgets`, PATCH `/budgets/{id}`, DELETE `/budgets/{id}`
- GET `/budget-summary?month=YYYY-MM`, POST `/budgets/{month}/copy`
- GET `/assets?date=YYYY-MM-DD`, POST `/asset-snapshots`
- GET `/liabilities`, POST `/liabilities`, POST `/liability-snapshots`
- GET `/holdings?account_id=...`, POST `/holdings`, GET `/portfolio-summary`
- GET `/dividends?year=YYYY`, POST `/dividends`, PATCH `/dividends/{id}`, GET `/dividend-summary?year=YYYY`
- POST `/realized-gains`
- GET `/closings`, GET `/closings/{month}`, POST `/closings/{month}/preview`, POST `/closings/{month}/finalize`
- GET `/net-worth/history`; GET/PATCH `/settings`
- JSON responses, authentication required; input validation and common HTTP statuses: 400, 401, 403, 404, 409, 422, 429, 503. Include request-id and expected_version on mutation.

## Pages
1. Dashboard: assets/liabilities/net worth, budget and portfolio KPIs, charts.
2. Monthly Budget: editable budget categories and recurring items.
3. Assets & Debts: balances and loan snapshots.
4. Investments: account/holding market value, unrealized/realized gains, dividends.
5. Dividends: monthly/yearly aggregation and ledger.
6. History & Monthly Close: preview, validation, finalize and revision history.
7. Settings: accounts, taxonomy, authentication and backup controls.

## Suggested deployment stack
Python 3 + Flask Blueprints + gspread + Pydantic + Jinja2 + Bootstrap + Chart.js, hosted with PythonAnywhere WSGI. Use Flask-Login (or equivalent secure sessions), CSRF protection, secret env vars / untracked service-account JSON (never under static web root), HTTPS, and dependency pinning. Cache reads briefly and invalidate after changes. Implement tests with a fake Sheets client before production writes.

## Immediate Codex milestones
1. Generate application skeleton and `.env.example` / `requirements.txt` / README deployment steps.
2. Implement Sheets client and schema/header verification (fail closed on mismatch).
3. Implement authenticated read-only API and dashboard using empty-state handling.
4. Implement writes, input validation, versions, and basic formulas.
5. Implement monthly close with revisioning and crash recovery.
6. Deploy to PythonAnywhere when explicitly requested; do not claim deployed before user credentials/hosting access is configured.

## Scope restrictions
- Do NOT access or import ClevMoney data yet (Drive backup `.db` files are future optional sources).
- Do NOT alter original existing workbook.
- Do NOT automatically migrate reference financial figures into the new Sheet.
- Do NOT add synthetic transaction rows to the operational datastore.
