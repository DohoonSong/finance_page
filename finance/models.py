from datetime import date
from typing import Literal, Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator, model_validator

Money = Annotated[StrictInt, Field(ge=0, le=9_000_000_000_000)]
SignedMoney = Annotated[StrictInt, Field(ge=-9_000_000_000_000, le=9_000_000_000_000)]
Text = Annotated[str, Field(min_length=1, max_length=200)]
Currency = Literal['KRW', 'USD']
Flow = Literal['income', 'expense', 'investment', 'transfer', 'debt_principal']
Month = Annotated[str, Field(pattern=r'^\d{4}-(0[1-9]|1[0-2])$')]


class Record(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

    @field_validator('*', mode='before')
    @classmethod
    def date_strings(cls, v, info):
        if info.field_name in ('date', 'payment_date', 'trade_date', 'start_date', 'maturity_date'):
            if not isinstance(v, str) or len(v) != 10:
                raise ValueError('날짜는 YYYY-MM-DD 형식이어야 합니다.')
        if info.field_name in ('month', 'start_month', 'end_month') and v:
            if not isinstance(v, str):
                raise ValueError('월은 YYYY-MM 형식이어야 합니다.')
            date.fromisoformat(v + '-01')
        return v


class Account(Record):
    name: Text
    type: Literal['bank', 'cash', 'brokerage', 'retirement', 'other']
    institution: str = Field(default='', max_length=200)
    currency: Currency = 'KRW'
    active: StrictBool = True


class AssetItem(Record):
    account_id: UUID
    category: Literal['cash', 'deposit', 'property', 'other']
    name: Text
    active: StrictBool = True


class Budget(Record):
    month: Month
    category: Text
    item: Text
    amount: SignedMoney
    flow_type: Flow
    note: str = Field(default='', max_length=1000)


class Recurring(Record):
    item: Text
    category: Text
    flow_type: Flow
    day: Annotated[StrictInt, Field(ge=1, le=31)]
    amount: Money
    start_month: Month
    end_month: Month | Literal[''] = ''
    active: StrictBool = True

    @model_validator(mode='after')
    def period(self):
        if self.end_month and self.end_month < self.start_month:
            raise ValueError('종료월은 시작월 이후여야 합니다.')
        return self


class AssetSnapshot(Record):
    date: date
    asset_id: UUID
    amount: Money
    note: str = Field(default='', max_length=1000)


class Liability(Record):
    name: Text
    original_principal: Money
    annual_rate: float = Field(ge=0, le=1, allow_inf_nan=False, strict=True)
    start_date: date
    maturity_date: date
    active: StrictBool = True

    @model_validator(mode='after')
    def period(self):
        if self.maturity_date < self.start_date:
            raise ValueError('만기일은 시작일 이후여야 합니다.')
        return self


class LiabilitySnapshot(Record):
    date: date
    loan_id: UUID
    balance: Money


class Holding(Record):
    date: date
    account_id: UUID
    symbol: Text
    name: Text
    cost_basis: Money
    market_value: Money
    currency: Currency


class Dividend(Record):
    payment_date: date
    account_id: UUID
    symbol: Text
    gross_amount: Money
    tax_amount: Money
    net_amount: Money
    currency: Currency
    note: str = Field(default='', max_length=1000)

    @model_validator(mode='after')
    def net(self):
        if self.gross_amount - self.tax_amount != self.net_amount:
            raise ValueError('순배당 = 총배당 - 세금이어야 합니다.')
        return self


class Gain(Record):
    trade_date: date
    account_id: UUID
    symbol: Text
    profit: SignedMoney
    currency: Currency
    note: str = Field(default='', max_length=1000)


MODELS = dict(zip(('accounts', 'asset_items', 'budget_items', 'recurring_items', 'asset_snapshots', 'liabilities', 'liability_snapshots', 'holdings', 'dividends', 'realized_gains'), (Account, AssetItem, Budget, Recurring, AssetSnapshot, Liability, LiabilitySnapshot, Holding, Dividend, Gain)))
