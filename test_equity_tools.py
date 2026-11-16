"""Offline contract and financial-data correctness checks."""

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import pandas as pd

from demo_tools_provider import DemoTicker
from equity_tools import ToolClient


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.client = ToolClient(self.temp.name, ticker_factory=DemoTicker, demo=True)

    def financials(self, client=None, **kwargs):
        return (client or self.client).call_tool("get_financial_statements", {
            "symbol": "AAPL", "frequency": "annual", "periods": 2, **kwargs})

    def test_demo_tools_are_json_safe_and_do_not_call_yahoo(self):
        with patch("yfinance.Ticker", side_effect=AssertionError("network not allowed")):
            results = [self.client.call_tool("get_price_history", {
                "symbol": "AAPL", "start": "2024-01-02", "end": "2024-01-04"}),
                self.client.call_tool("get_company_profile", {"symbol": "AAPL"}), self.financials()]
        for result in results:
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["data_kind"], "synthetic_demo")
            json.dumps(result, allow_nan=False)
            self.assertTrue(all(source["source_url"] is None for source in result["sources"]))

    def test_invalid_arguments_do_not_contact_provider(self):
        factory = Mock(side_effect=AssertionError("should not be called"))
        client = ToolClient(self.temp.name, ticker_factory=factory)
        bad = [
            ("get_company_profile", {"symbol": "INVALID"}),
            ("get_company_profile", {"symbol": "AAPL", "url": "https://example.com"}),
            ("get_financial_statements", {"symbol": "AAPL", "frequency": "annual", "periods": True}),
            ("get_financial_statements", {"symbol": "AAPL", "frequency": "TTM", "periods": 2}),
            ("get_financial_statements", {"symbol": "AAPL", "frequency": "annual", "periods": 9}),
            ("get_price_history", {"symbol": "AAPL", "start": "2024-02-30", "end": "2024-03-01"}),
            ("get_price_history", {"symbol": "AAPL", "start": "2024-01-02", "end": "2024-01-02"}),
        ]
        for name, arguments in bad:
            with self.subTest(arguments=arguments):
                result = client.call_tool(name, arguments)
                self.assertEqual(result["error"]["code"], "INVALID_ARGUMENTS")
        factory.assert_not_called()
        self.assertEqual(client.call_tool("eval", {})["error"]["code"], "UNKNOWN_TOOL")

    def test_financial_currency_zero_sign_and_order(self):
        result = self.financials()
        data = result["data"]
        self.assertEqual(data["financial_currency"], "DEMO_REPORT")
        income = data["statements"]["income_statement"]["periods"]
        self.assertEqual(income[0]["period_end"], "2024-12-31")
        self.assertEqual(income[1]["metrics"]["net_income"]["value"], 0.0)
        self.assertEqual(income[0]["metrics"]["diluted_eps"]["unit"], "DEMO_REPORT/share")
        self.assertIsNone(income[0]["filing_date"])
        cash = data["statements"]["cash_flow"]["periods"][0]
        self.assertEqual(cash["metrics"]["capital_expenditure"]["value"], -15.0)

    def test_nan_becomes_null_and_partial(self):
        class MissingMetric(DemoTicker):
            def get_income_stmt(self, **kwargs):
                table = super().get_income_stmt(**kwargs)
                table.loc["Net Income", :] = float("nan")
                return table
        client = ToolClient(self.temp.name, ticker_factory=MissingMetric, demo=True)
        result = self.financials(client)
        self.assertEqual(result["status"], "partial")
        value = result["data"]["statements"]["income_statement"]["periods"][0]["metrics"]["net_income"]["value"]
        self.assertIsNone(value)
        json.dumps(result, allow_nan=False)

    def test_quote_currency_is_not_used_as_reporting_currency(self):
        class NoCurrency(DemoTicker):
            def get_info(self):
                return {"longName": "Demo", "currency": "QUOTE_ONLY"}
        client = ToolClient(self.temp.name, ticker_factory=NoCurrency, demo=True)
        result = self.financials(client)
        self.assertEqual(result["status"], "partial")
        self.assertIsNone(result["data"]["financial_currency"])
        self.assertIsNone(result["data"]["statements"]["income_statement"]["periods"][0]["metrics"]["revenue"]["unit"])

    def test_one_statement_failure_keeps_other_two(self):
        class BadCashFlow(DemoTicker):
            def get_cash_flow(self, **kwargs):
                raise TimeoutError("sensitive request info must not leak")
        client = ToolClient(self.temp.name, ticker_factory=BadCashFlow, demo=True)
        result = self.financials(client)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["data"]["statements"]["cash_flow"]["status"], "unavailable")
        self.assertEqual(result["data"]["statements"]["balance_sheet"]["status"], "ok")
        self.assertNotIn("sensitive request", json.dumps(result))

    def test_all_statements_empty_returns_no_data(self):
        class Empty(DemoTicker):
            def get_income_stmt(self, **kwargs): return pd.DataFrame()
            def get_balance_sheet(self, **kwargs): return pd.DataFrame()
            def get_cash_flow(self, **kwargs): return pd.DataFrame()
        client = ToolClient(self.temp.name, ticker_factory=Empty, demo=True)
        result = self.financials(client)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"]["code"], "NO_DATA")
        self.assertIsNone(result["data"])

    def test_price_uses_adjusted_series_and_excludes_end(self):
        result = self.client.call_tool("get_price_history", {
            "symbol": "AAPL", "start": "2024-01-02", "end": "2024-01-04"})
        self.assertEqual(result["data"]["summary"]["adjusted_close_change_pct"], 0.0)
        bad = self.client.call_tool("get_price_history", {
            "symbol": "AAPL", "start": "2024-01-02", "end": "2024-01-03"})
        self.assertEqual(bad["error"]["code"], "INVALID_PROVIDER_DATA")

    def test_sources_and_saved_files_are_consistent_and_portable(self):
        result = self.financials()
        ids = [source["source_id"] for source in result["sources"]]
        self.assertEqual(len(ids), len(set(ids)))
        for source in result["sources"]:
            path = self.client.output_dir / source["raw_path"]
            self.assertFalse(Path(source["raw_path"]).is_absolute())
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), source["sha256"])
        result_file = self.client.output_dir / result["artifacts"][-1]
        self.assertEqual(json.loads(result_file.read_text()), result)
        for statement in result["data"]["statements"].values():
            for period in statement["periods"]:
                source = next(s for s in result["sources"] if s["source_id"] == period["source_id"])
                table = pd.read_csv(self.client.output_dir / source["raw_path"], index_col=0)
                for metric in period["metrics"].values():
                    if metric["value"] is not None:
                        self.assertEqual(float(table.at[metric["source_row"], metric["source_column"]]), metric["value"])

    def test_less_history_is_partial(self):
        result = self.financials(periods=4)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["data"]["statements"]["income_statement"]["periods"]), 2)

    def test_native_camelcase_labels_and_integer_valued_float(self):
        class NativeLabels(DemoTicker):
            def get_income_stmt(self, **kwargs):
                table = super().get_income_stmt(**kwargs)
                return table.rename(index=lambda label: label.replace(" ", ""))
        client = ToolClient(self.temp.name, ticker_factory=NativeLabels, demo=True)
        result = self.financials(client, periods=2.0)
        self.assertEqual(result["status"], "ok")
        revenue = result["data"]["statements"]["income_statement"]["periods"][0]["metrics"]["revenue"]
        self.assertEqual(revenue["value"], 120.0)
        self.assertEqual(revenue["source_row"], "TotalRevenue")

    def test_storage_failure_is_not_reported_as_empty_financials(self):
        blocked = Path(self.temp.name) / "a-file-not-a-directory"
        blocked.write_text("occupied")
        client = ToolClient(blocked, ticker_factory=DemoTicker, demo=True)
        result = self.financials(client)
        self.assertEqual(result["error"]["code"], "STORAGE_ERROR")
        self.assertFalse(result["error"]["retryable"])


if __name__ == "__main__":
    unittest.main()
