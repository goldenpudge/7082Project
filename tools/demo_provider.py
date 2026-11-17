"""Clearly synthetic provider for teaching and offline integration tests."""

import pandas as pd


class DemoTicker:
    def __init__(self, symbol):
        self.symbol = symbol

    def get_info(self):
        return {"longName": "SYNTHETIC DEMO - NOT REAL COMPANY DATA",
                "currency": "DEMO_QUOTE", "financialCurrency": "DEMO_REPORT",
                "sector": "Teaching", "industry": "Teaching",
                "longBusinessSummary": "虚构教学样例，不反映 AAPL/NVDA/PLTR 的实际情况。",
                "marketCap": 1000, "currentPrice": 99, "trailingPE": None}

    def history(self, **kwargs):
        return pd.DataFrame({"Close": [100.0, 99.0], "Adj Close": [99.0, 99.0],
                             "Volume": [1000, 2000]},
                            index=pd.to_datetime(["2024-01-02", "2024-01-03"]))

    def get_history_metadata(self):
        return {"currency": "DEMO_QUOTE", "exchangeTimezoneName": "UTC"}

    def get_income_stmt(self, freq="yearly"):
        return self._table({"Total Revenue": [100, 120], "Gross Profit": [50, 60],
                            "Operating Income": [20, 30], "Net Income": [0, 10],
                            "Diluted EPS": [0, 0.1]})

    def get_balance_sheet(self, freq="yearly"):
        return self._table({"Total Assets": [200, 250],
                            "Total Liabilities Net Minority Interest": [80, 90],
                            "Stockholders Equity": [120, 160],
                            "Cash Cash Equivalents And Short Term Investments": [40, 60],
                            "Total Debt": [20, 15]})

    def get_cash_flow(self, freq="yearly"):
        return self._table({"Operating Cash Flow": [30, 40],
                            "Capital Expenditure": [-10, -15], "Free Cash Flow": [20, 25]})

    @staticmethod
    def _table(rows):
        # Deliberately oldest-first to test period ordering.
        return pd.DataFrame.from_dict(rows, orient="index",
                                      columns=pd.to_datetime(["2023-12-31", "2024-12-31"]))
