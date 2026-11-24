"""Which companies we track and which SEC (XBRL) tags hold each number.

Each metric has a list of candidate tags, tried in order. Companies sometimes
switch tags over the years, so we merge them: for any given quarter, the first
tag in the list that has a value wins.
"""

START_DATE = "2019-10-01"  # keep a little before 2020 so 2020 growth rates work

COMPANIES = {
    "MSFT": {"name": "Microsoft", "cik": "0000789019", "role": "spender", "fiscal_year_end": "June"},
    "GOOGL": {"name": "Alphabet", "cik": "0001652044", "role": "spender", "fiscal_year_end": "December"},
    "AMZN": {"name": "Amazon", "cik": "0001018724", "role": "spender", "fiscal_year_end": "December"},
    "META": {"name": "Meta", "cik": "0001326801", "role": "spender", "fiscal_year_end": "December"},
    "ORCL": {"name": "Oracle", "cik": "0001341439", "role": "spender", "fiscal_year_end": "May"},
    "NVDA": {"name": "Nvidia", "cik": "0001045810", "role": "seller", "fiscal_year_end": "late January"},
}

DEFAULT_TAGS = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ],
    "operating_cash_flow": [
        "NetCashProvidedByUsedInOperatingActivities",
    ],
    # "Purchases of property and equipment" on the cash flow statement.
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
    ],
    # Extra spending on equipment paid for through finance leases (not in capex line).
    "finance_lease_principal": [
        "FinanceLeasePrincipalPayments",
        "RepaymentsOfLongTermCapitalLeaseObligations",
    ],
}

# Per-company overrides where a company uses a different tag for the same line.
TAG_OVERRIDES = {
    # Amazon labels its line "Purchases of property and equipment" but tags it this way.
    "AMZN": {
        "capex": ["PaymentsToAcquireProductiveAssets", "PaymentsToAcquirePropertyPlantAndEquipment"],
    },
}


def tags_for(ticker: str, metric: str) -> list[str]:
    return TAG_OVERRIDES.get(ticker, {}).get(metric, DEFAULT_TAGS[metric])
