"""离线检查金融计算与来源文件；不消耗模型额度。"""

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pandas as pd

from market_data import COMPANIES, get_price, resolve_company, run_demo, summarize_prices


def dividend_example():
    return pd.DataFrame(
        {"Close": [100.0, 99.0], "Adj Close": [99.0, 99.0], "Volume": [1000, 2000]},
        index=pd.to_datetime(["2024-01-02", "2024-01-03"]),
    )


class PriceToolTests(unittest.TestCase):
    def test_dividend_adjustment_does_not_create_false_loss(self):
        result = summarize_prices(dividend_example().iloc[::-1])
        self.assertAlmostEqual(result["adjusted_close_change_pct"], 0.0)
        self.assertEqual(result["last_close"], 99.0)
        self.assertEqual(result["mean_daily_volume_shares"], 1500.0)

    def test_missing_and_invalid_data_are_not_zero(self):
        bad_tables = [dividend_example().iloc[:0], dividend_example().drop(columns="Adj Close")]
        for column, value in [("Close", float("nan")), ("Adj Close", 0), ("Volume", -1)]:
            table = dividend_example()
            table.loc[table.index[0], column] = value
            bad_tables.append(table)
        for table in bad_tables:
            with self.subTest(table=table), self.assertRaises(ValueError):
                summarize_prices(table)

    def test_company_list_and_symbols(self):
        self.assertEqual(len(COMPANIES), 15)
        self.assertEqual(resolve_company("688256.SH")["yahoo_symbol"], "688256.SS")
        self.assertEqual(resolve_company(" pltr ")["course_symbol"], "PLTR")
        with self.assertRaises(ValueError):
            resolve_company("NOT_IN_COURSE")

    def test_demo_is_labelled_and_files_can_be_verified(self):
        with TemporaryDirectory() as folder:
            result = run_demo(folder)
            self.assertEqual(result["source"]["kind"], "SYNTHETIC_TEACHING_DATA")
            self.assertIsNone(result["source"]["source_url"])
            for name, digest in result["source"]["files_sha256"].items():
                raw = (Path(result["evidence_dir"]) / name).read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)

    def test_yahoo_parameters_and_provenance(self):
        with TemporaryDirectory() as folder, patch("yfinance.Ticker") as factory:
            ticker = factory.return_value
            ticker.history.return_value = dividend_example()
            ticker.get_history_metadata.return_value = {"currency": "USD", "exchangeTimezoneName": "America/New_York"}
            result = get_price("AAPL", "2024-01-02", "2024-01-04", folder)
            self.assertFalse(ticker.history.call_args.kwargs["auto_adjust"])
            self.assertEqual(ticker.history.call_args.kwargs["end"], "2024-01-04")
            self.assertEqual(result["summary"]["currency"], "USD")
            saved = json.loads((Path(result["evidence_dir"]) / "provenance.json").read_text())
            self.assertEqual(saved["requested_end_exclusive"], "2024-01-04")

    def test_end_date_is_exclusive(self):
        with TemporaryDirectory() as folder, patch("yfinance.Ticker") as factory:
            factory.return_value.history.return_value = dividend_example()
            with self.assertRaises(ValueError):
                get_price("AAPL", "2024-01-02", "2024-01-03", folder)


if __name__ == "__main__":
    unittest.main()
