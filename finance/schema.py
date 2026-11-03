"""The handoff's exact schema. Derived tabs never accept user records."""
SCHEMA = {
    'accounts': 'account_id name type institution currency active created_at updated_at version',
    'asset_items': 'asset_id account_id category name active created_at updated_at version',
    'budget_items': 'budget_id month category item amount flow_type note created_at updated_at version',
    'recurring_items': 'recurring_id item category flow_type day amount start_month end_month active updated_at version',
    'asset_snapshots': 'snapshot_id date asset_id amount note created_at updated_at version',
    'liabilities': 'loan_id name original_principal annual_rate start_date maturity_date active updated_at version',
    'liability_snapshots': 'snapshot_id date loan_id balance created_at updated_at version',
    'holdings': 'holding_id date account_id symbol name cost_basis market_value currency updated_at version',
    'dividends': 'dividend_id payment_date account_id symbol gross_amount tax_amount net_amount currency note created_at updated_at version',
    'realized_gains': 'gain_id trade_date account_id symbol profit currency note created_at updated_at version',
    'monthly_closings': 'closing_id month status revision assets_total liabilities_total net_worth budget_balance portfolio_profit data_hash closed_at created_at',
    'closing_details': 'closing_id revision item_type item_id amount as_of_date source_ref',
    'calc_budget': 'month income_total expense_total investment_allocation budget_balance',
    'calc_portfolio': 'account_id cost_basis market_value unrealized_profit dividend_total realized_profit total_profit simple_return_pct',
    'settings': 'key value value_type description updated_at',
}
SCHEMA = {k: v.split() for k, v in SCHEMA.items()}
INPUT_TABLES = tuple(k for k in SCHEMA if k not in ('monthly_closings', 'closing_details', 'calc_budget', 'calc_portfolio', 'settings'))
FLOWS = ('income', 'expense', 'investment', 'transfer', 'debt_principal')


class AppError(Exception):
    def __init__(self, message, status=422, code='validation_error'):
        super().__init__(message)
        self.status, self.code = status, code
