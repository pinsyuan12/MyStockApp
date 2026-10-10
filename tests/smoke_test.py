"""MyStockApp 無網路煙霧測試：檢查介面元件、資料庫與基本運算。"""
import os
import runpy
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

source = Path(__file__).resolve().parents[1] / "AlphaPulse Ultimate.py"

class FakePage:
    def __init__(self):
        self.controls = []
        self.update_count = 0

    def add(self, *controls):
        self.controls.extend(controls)

    def update(self):
        self.update_count += 1

with tempfile.TemporaryDirectory() as temp:
    previous_dir = os.getcwd()
    os.chdir(temp)
    try:
        app = runpy.run_path(str(source), run_name="smoke_test")
        assert app["StockService"].format_symbol("2330") == "2330.TW"
        assert app["StockService"].format_symbol("aapl") == "AAPL"
        assert app["SECTOR_ZH_TW"]["Technology"] == "資訊科技"
        assert app["AppColors"].UP == "#ef4444"  # 台股上漲為紅色

        db = app["DatabaseManager"]()
        assert db.add_to_watchlist("2330.TW") is True
        assert db.add_to_watchlist("2330.TW") is False
        assert db.get_watchlist() == ["2330.TW"]
        db.remove_from_watchlist("2330.TW")
        assert db.get_watchlist() == []
        db.conn.close()

        class Ticker:
            fast_info = SimpleNamespace(last_price=105.0, previous_close=100.0)

        with patch.object(app["yf"], "Ticker", return_value=Ticker()):
            quote = app["StockService"].get_quote("2330.TW")
        assert quote["price"] == 105.0
        assert quote["change"] == 5.0
        assert quote["pct"] == 5.0

        page = FakePage()
        class FakeDB:
            def get_watchlist(self):
                return []
        with patch.dict(app["main"].__globals__, {"DatabaseManager": FakeDB}):
            app["main"](page)
        assert page.title == "AlphaPulse Ultimate｜台股分析系統"
        assert len(page.controls) == 1
        assert len(page.navigation_bar.destinations) == 2
        assert page.update_count >= 1
        print("SMOKE TEST PASSED: symbol, translations, sqlite, quotes, page")
    finally:
        os.chdir(previous_dir)
