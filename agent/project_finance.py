"""Deterministic monthly project finance. All inputs are explicit assumptions."""
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP
from math import isfinite
from typing import Annotated, Literal

import numpy_financial as npf
from pydantic import BaseModel, ConfigDict, Field, field_validator


Money = Annotated[Decimal, Field(ge=0, le=10**12, decimal_places=2)]
Percent = Annotated[Decimal, Field(ge=0, le=100)]
Change = Annotated[Decimal, Field(ge=-100, le=1000)]
Delay = Annotated[int, Field(ge=0, le=24)]
Discount = Annotated[Decimal, Field(ge=-99, le=1000)]


class ModelInputs(BaseModel):
    model_config = ConfigDict(extra='forbid')

    months: int | None = Field(default=None, ge=1, le=60)
    currency: str | None = Field(default=None, pattern=r'^[A-Z]{3}$')
    opening_cash: Money | None = None
    capex: Money | None = None
    revenue_monthly: Money | None = None
    fixed_cost_monthly: Money | None = None
    variable_cost_pct: Percent | None = None
    payment_delay_months: Delay | None = None
    depreciation_months: int | None = Field(default=None, ge=0, le=600)
    discount_annual_pct: Discount | None = None


class Scenario(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=60)
    revenue_change_pct: Change = Decimal(0)
    fixed_change_pct: Change = Decimal(0)
    variable_cost_pct: Percent | None = None
    payment_delay_months: Delay | None = None
    advance_pct: Percent = Decimal(0)

    @field_validator('revenue_change_pct', 'fixed_change_pct', 'advance_pct', mode='before')
    @classmethod
    def zero_if_unspecified(cls, value):
        return 0 if value is None else value


class ModelRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    action: Literal['base', 'scenario', 'compare', 'investment', 'sensitivity', 'breakeven', 'advance', 'clarify']
    base: ModelInputs = Field(default_factory=ModelInputs)
    scenarios: list[Scenario] = Field(default_factory=list, max_length=8)
    scenario_names: list[Annotated[str, Field(min_length=1, max_length=60)]] = Field(default_factory=list, max_length=8)
    reset: bool = False
    question: str = Field(default='', max_length=600)


FIELDS = {
    'months': 'горизонт в месяцах', 'currency': 'валюта',
    'opening_cash': 'деньги на счёте до запуска', 'capex': 'разовые инвестиции в нулевом месяце',
    'revenue_monthly': 'ожидаемая месячная выручка', 'fixed_cost_monthly': 'постоянные расходы в месяц',
    'variable_cost_pct': 'переменные расходы в процентах от выручки',
    'payment_delay_months': 'отсрочка оплаты покупателей в месяцах (0 при оплате в месяц продажи)',
    'depreciation_months': 'срок амортизации оборудования в месяцах (0, если амортизацию исключаем)',
    'discount_annual_pct': 'эффективная годовая ставка дисконтирования в процентах',
}

DEMO = {
    'months': 12, 'currency': 'RUB', 'opening_cash': 3500000, 'capex': 3000000,
    'revenue_monthly': 1500000, 'fixed_cost_monthly': 600000, 'variable_cost_pct': 40,
    'payment_delay_months': 0, 'depreciation_months': 24, 'discount_annual_pct': 18,
}
BASELINE = Scenario(name='Базовый')
FORMULA = ('Операционная прибыль = выручка − переменные и постоянные расходы − амортизация. '
           'Остаток денег = предыдущий остаток + платежи покупателей − денежные расходы. '
           'Инвестиции списываются с остатка в месяце 0. NPV = Σ CFₜ / (1 + r)ᵗ, '
           'где месячная r = (1 + годовая ставка) ^ (1/12) − 1.')
ASSUMPTIONS = ('Выручка и регулярные расходы постоянны в пределах сценария. Расходы оплачиваются в месяц начисления. '
               'Амортизация линейная. Налоги, кредитование, проценты и остаточная стоимость исключены. '
               'Ликвидность проверяется на конец месяца. Поступления после горизонта показаны отдельно.')


def rounded(value, quantum='0.01'):
    return value.quantize(Decimal(quantum), rounding=ROUND_HALF_UP)


def number(value):
    return float(rounded(value)) if isinstance(value, Decimal) else value


def amount(value):
    return f'{rounded(value):,.2f}'.replace(',', ' ').replace('.', ',')


@dataclass
class Projection:
    rows: list
    minimum_cash: Decimal
    first_gap: int | None
    receivables: Decimal
    npv: Decimal | None
    irr_monthly: Decimal | None
    irr_annual: Decimal | None
    payback: int | None

    @property
    def profit(self):
        return sum((row['profit'] for row in self.rows), Decimal(0))

    @property
    def funding(self):
        return max(Decimal(0), -self.minimum_cash)


def parameters(base, scenario):
    revenue = base.revenue_monthly * (1 + scenario.revenue_change_pct / 100)
    fixed = base.fixed_cost_monthly * (1 + scenario.fixed_change_pct / 100)
    variable = base.variable_cost_pct if scenario.variable_cost_pct is None else scenario.variable_cost_pct
    delay = base.payment_delay_months if scenario.payment_delay_months is None else scenario.payment_delay_months
    depreciation = base.capex / base.depreciation_months if base.depreciation_months else Decimal(0)
    return revenue, fixed, variable, delay, depreciation


def project(base, scenario):
    revenue, fixed, variable_pct, delay, depreciation = parameters(base, scenario)
    advance = scenario.advance_pct / 100
    cash = base.opening_cash - base.capex
    costs = fixed + revenue * variable_pct / 100
    rows = [{'month': 0, 'revenue': Decimal(0), 'receipts': Decimal(0), 'costs': Decimal(0),
             'depreciation': Decimal(0), 'profit': Decimal(0), 'flow': -base.capex, 'cash': cash}]
    for month in range(1, base.months + 1):
        receipts = revenue * (advance + (1 - advance if month > delay else 0))
        amort = depreciation if month <= (base.depreciation_months or 0) else Decimal(0)
        flow = receipts - costs
        cash += flow
        rows.append({'month': month, 'revenue': revenue, 'receipts': receipts, 'costs': costs,
                     'depreciation': amort, 'profit': revenue - costs - amort, 'flow': flow, 'cash': cash})
    flows = [row['flow'] for row in rows]
    npv = None
    if base.discount_annual_pct is not None:
        monthly = (1 + float(base.discount_annual_pct) / 100) ** (1 / 12) - 1
        npv = Decimal(str(npf.npv(monthly, [float(flow) for flow in flows])))
    signs = [flow > 0 for flow in flows if flow]
    irr_monthly = irr_annual = None
    if base.capex > 0 and sum(a != b for a, b in zip(signs, signs[1:])) == 1:
        irr = float(npf.irr([float(flow) for flow in flows]))
        if isfinite(irr) and irr > -1:
            irr_monthly = Decimal(str(irr)) * 100
            irr_annual = ((1 + Decimal(str(irr))) ** 12 - 1) * 100
    accumulated = Decimal(0)
    payback = None
    for row in rows:
        accumulated += row['flow']
        if accumulated >= 0:
            payback = row['month']
            break
    return Projection(rows, min(row['cash'] for row in rows),
                      next((row['month'] for row in rows if row['cash'] < 0), None),
                      min(base.months, delay) * revenue * (1 - advance), npv, irr_monthly, irr_annual, payback)


def minimum_advance(base, scenario):
    scenario = scenario.model_copy(update={'advance_pct': Decimal(0)})
    projection = project(base, scenario)
    revenue, _, _, delay, _ = parameters(base, scenario)
    required = Decimal(0)
    for row in projection.rows:
        if row['cash'] >= 0:
            continue
        unpaid = min(row['month'], delay) * revenue
        if unpaid == 0:
            return None
        required = max(required, -row['cash'] / unpaid)
    return (required * 100).quantize(Decimal('0.01'), rounding=ROUND_CEILING) if required <= 1 else None


def breakeven(base):
    margin = 1 - base.variable_cost_pct / 100
    depreciation = base.capex / base.depreciation_months if base.depreciation_months else Decimal(0)
    if not margin:
        operating = None if base.fixed_cost_monthly + depreciation else Decimal(0)
        cash = None if base.fixed_cost_monthly else Decimal(0)
        return operating, cash
    return (base.fixed_cost_monthly + depreciation) / margin, base.fixed_cost_monthly / margin
