import flet as ft
import yfinance as yf
import mplfinance as mpf
import pandas as pd
import sqlite3
import io
import base64
import matplotlib
import threading
import datetime

# --- 0. 全局設定與常數 ---
matplotlib.use('Agg')  # 設定無頭模式，避免彈出視窗
DB_NAME = "alphapulse_v2.db"

# 色票系統 (Fintech 風格)
class AppColors:
    BG = "#0f172a"          # 深藍黑背景
    SURFACE = "#1e293b"     # 卡片背景
    PRIMARY = "#3b82f6"     # 主色調藍
    UP = "#ef4444"          # 漲 (台股紅)
    DOWN = "#22c55e"        # 跌 (台股綠)
    TEXT_MAIN = "#f8fafc"   # 主要文字
    TEXT_SUB = "#94a3b8"    # 次要文字
    DIVIDER = "#334155"

# --- 1. 模型層 (Model & Database) ---
class DatabaseManager:
    """處理所有 SQLite 資料庫操作"""
    def __init__(self):
        self.conn = sqlite3.connect(DB_NAME, check_same_thread=False)
        self.create_tables()

    def create_tables(self):
        cursor = self.conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS watchlist (
                symbol TEXT PRIMARY KEY,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.conn.commit()

    def add_to_watchlist(self, symbol):
        try:
            cursor = self.conn.cursor()
            cursor.execute("INSERT INTO watchlist (symbol) VALUES (?)", (symbol,))
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def remove_from_watchlist(self, symbol):
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM watchlist WHERE symbol = ?", (symbol,))
        self.conn.commit()

    def get_watchlist(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT symbol FROM watchlist ORDER BY added_at DESC")
        return [row[0] for row in cursor.fetchall()]

# --- 2. 服務層 (Data Service) ---
class StockService:
    """處理 Yahoo Finance API 所有請求"""
    
    @staticmethod
    def format_symbol(code):
        code = code.strip().upper()
        # 簡單判斷：如果是數字且不含後綴，預設加上 .TW
        if code.isdigit():
            return f"{code}.TW"
        return code

    @staticmethod
    def get_quote(symbol):
        try:
            ticker = yf.Ticker(symbol)
            # 使用 fast_info 獲取即時數據 (比 history 快)
            price = ticker.fast_info.last_price
            prev_close = ticker.fast_info.previous_close
            
            if not price: return None
            
            change = price - prev_close
            pct = (change / prev_close) * 100
            
            return {
                "symbol": symbol,
                "price": price,
                "change": change,
                "pct": pct,
                "prev_close": prev_close
            }
        except:
            return None

    @staticmethod
    def get_details(symbol):
        """獲取詳細基本面資料"""
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info
            return {
                "name": info.get("longName", symbol),
                "sector": info.get("sector", "N/A"),
                "pe": info.get("trailingPE", "N/A"),
                "eps": info.get("trailingEps", "N/A"),
                "mkt_cap": info.get("marketCap", 0),
                "volume": info.get("volume", 0),
                "high": info.get("dayHigh", 0),
                "low": info.get("dayLow", 0),
            }
        except:
            return None

    @staticmethod
    def get_news(symbol):
        """獲取新聞列表"""
        try:
            ticker = yf.Ticker(symbol)
            return ticker.news[:5] # 只取前5則
        except:
            return []

    @staticmethod
    def generate_chart_image(symbol):
        """生成 K 線圖 Base64"""
        try:
            df = yf.download(symbol, period="6mo", interval="1d", progress=False)
            if df.empty: return None

            # 設定圖表風格
            mc = mpf.make_marketcolors(up='r', down='g', inherit=True)
            s = mpf.make_mpf_style(base_mpf_style='nightclouds', marketcolors=mc, gridstyle=':')
            
            buf = io.BytesIO()
            mpf.plot(
                df, 
                type='candle', 
                style=s, 
                mav=(5, 20, 60), 
                volume=True,
                title=f'\n{symbol} Daily Chart',
                savefig=dict(fname=buf, dpi=100, bbox_inches='tight', transparent=True),
                ylabel='',     # 省略 Y 軸標籤以節省手機空間
                ylabel_lower=''
            )
            buf.seek(0)
            return base64.b64encode(buf.read()).decode()
        except Exception as e:
            print(f"Chart Error: {e}")
            return None

# --- 3. UI 組件層 (Components) ---
class StockCard(ft.UserControl):
    """自選股列表中的單張卡片"""
    def __init__(self, symbol, data, on_delete_click, on_card_click):
        super().__init__()
        self.symbol = symbol
        self.data = data
        self.on_delete_click = on_delete_click
        self.on_card_click = on_card_click

    def build(self):
        color = AppColors.UP if self.data['change'] > 0 else AppColors.DOWN
        icon = ft.icons.TRENDING_UP if self.data['change'] > 0 else ft.icons.TRENDING_DOWN
        
        return ft.Container(
            padding=15,
            border_radius=15,
            bgcolor=AppColors.SURFACE,
            on_click=lambda e: self.on_card_click(self.symbol),
            content=ft.Row([
                ft.Column([
                    ft.Text(self.symbol, size=18, weight="bold", color=AppColors.TEXT_MAIN),
                    ft.Text(f"Prev: {self.data['prev_close']:.2f}", size=12, color=AppColors.TEXT_SUB),
                ]),
                ft.Row([
                    ft.Column([
                        ft.Text(f"${self.data['price']:.2f}", size=18, weight="bold", color=AppColors.TEXT_MAIN, text_align="right"),
                        ft.Text(f"{self.data['change']:+.2f} ({self.data['pct']:+.2f}%)", color=color, size=14, weight="bold", text_align="right"),
                    ], alignment="end"),
                    ft.IconButton(
                        icon=ft.icons.DELETE_OUTLINE, 
                        icon_color=AppColors.TEXT_SUB,
                        on_click=lambda e: self.on_delete_click(self.symbol)
                    )
                ])
            ], alignment="spaceBetween")
        )

# --- 4. 主程式邏輯 (Main Controller) ---
def main(page: ft.Page):
    # --- 頁面初始化 ---
    page.title = "AlphaPulse Ultimate"
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = AppColors.BG
    page.padding = 0
    page.window_width = 420
    page.window_height = 880

    db = DatabaseManager()
    
    # --- UI 狀態變數 ---
    current_symbol = None

    # --- 畫面元件宣告 ---
    
    # 1. 自選股列表視圖
    lv_watchlist = ft.ListView(expand=True, spacing=12, padding=20)
    
    def refresh_watchlist():
        lv_watchlist.controls.clear()
        # 顯示載入中
        lv_watchlist.controls.append(ft.ProgressBar(width=100, color=AppColors.PRIMARY, bgcolor=AppColors.SURFACE))
        page.update()
        
        symbols = db.get_watchlist()
        if not symbols:
            lv_watchlist.controls.clear()
            lv_watchlist.controls.append(
                ft.Column([
                    ft.Icon(ft.icons.DASHBOARD_CUSTOMIZE, size=60, color=AppColors.TEXT_SUB),
                    ft.Text("尚無自選股", color=AppColors.TEXT_SUB),
                    ft.Text("請至「個股分析」頁面添加", color=AppColors.TEXT_SUB, size=12)
                ], alignment="center", horizontal_alignment="center", expand=True)
            )
        else:
            lv_watchlist.controls.clear()
            for s in symbols:
                data = StockService.get_quote(s)
                if data:
                    card = StockCard(s, data, on_delete_stock, load_analysis_page)
                    lv_watchlist.controls.append(card)
        page.update()

    def on_delete_stock(symbol):
        db.remove_from_watchlist(symbol)
        refresh_watchlist()
        page.show_snack_bar(ft.SnackBar(content=ft.Text(f"{symbol} 已移除"), bgcolor=AppColors.SURFACE))

    view_watchlist = ft.Column([
        ft.Container(
            padding=ft.padding.only(left=20, top=50, bottom=10),
            content=ft.Text("My Watchlist", size=32, weight="900", color=AppColors.TEXT_MAIN)
        ),
        lv_watchlist
    ], expand=True)

    # 2. 個股分析視圖
    txt_search = ft.TextField(
        hint_text="輸入代碼 (例如 2330)", 
        border_radius=12,
        bgcolor=AppColors.SURFACE,
        color=AppColors.TEXT_MAIN,
        border_color=AppColors.SURFACE,
        focused_border_color=AppColors.PRIMARY,
        expand=True,
        on_submit=lambda e: run_analysis(txt_search.value)
    )
    
    # 分析頁的內容容器
    chart_container = ft.Image(src_base64=None, fit=ft.ImageFit.CONTAIN, visible=False)
    info_row = ft.Row(alignment="spaceBetween", wrap=True) # 基本面
    news_col = ft.Column(spacing=10) # 新聞
    
    # 分析頁頭部 (價格與收藏按鈕)
    lbl_detail_price = ft.Text("-", size=36, weight="bold")
    lbl_detail_change = ft.Text("-", size=16, weight="bold")
    btn_fav = ft.IconButton(icon=ft.icons.STAR_BORDER, icon_size=30, on_click=lambda e: toggle_fav())
    
    header_section = ft.Container(
        padding=20,
        bgcolor=AppColors.SURFACE,
        border_radius=ft.border_radius.only(bottom_left=20, bottom_right=20),
        content=ft.Column([
            ft.Row([txt_search, ft.IconButton(ft.icons.SEARCH, on_click=lambda e: run_analysis(txt_search.value))]),
            ft.Divider(height=20, color="transparent"),
            ft.Row([
                ft.Column([lbl_detail_price, lbl_detail_change]),
                btn_fav
            ], alignment="spaceBetween")
        ])
    )

    # 分頁內容 (Tabs)
    tabs_content = ft.Tabs(
        selected_index=0,
        animation_duration=300,
        indicator_color=AppColors.PRIMARY,
        label_color=AppColors.PRIMARY,
        unselected_label_color=AppColors.TEXT_SUB,
        tabs=[
            ft.Tab(text="K線圖", content=ft.Container(content=chart_container, padding=10)),
            ft.Tab(text="基本面", content=ft.Container(content=info_row, padding=20)),
            ft.Tab(text="新聞", content=ft.Container(content=ft.Column([news_col], scroll="auto"), padding=20)),
        ],
        expand=True,
        visible=False
    )
    
    loading_indicator = ft.ProgressRing(visible=False, color=AppColors.PRIMARY)

    view_analysis = ft.Column([
        header_section,
        ft.Container(content=loading_indicator, alignment=ft.alignment.center, height=50),
        tabs_content
    ], expand=True)

    # --- 邏輯函數 ---

    def toggle_fav():
        if not current_symbol: return
        
        watchlist = db.get_watchlist()
        if current_symbol in watchlist:
            db.remove_from_watchlist(current_symbol)
            btn_fav.icon = ft.icons.STAR_BORDER
            btn_fav.icon_color = AppColors.TEXT_SUB
            page.show_snack_bar(ft.SnackBar(content=ft.Text("已取消收藏")))
        else:
            db.add_to_watchlist(current_symbol)
            btn_fav.icon = ft.icons.STAR
            btn_fav.icon_color = "yellow"
            page.show_snack_bar(ft.SnackBar(content=ft.Text("已加入收藏")))
        page.update()

    def update_fav_icon(symbol):
        if symbol in db.get_watchlist():
            btn_fav.icon = ft.icons.STAR
            btn_fav.icon_color = "yellow"
        else:
            btn_fav.icon = ft.icons.STAR_BORDER
            btn_fav.icon_color = AppColors.TEXT_SUB

    def build_stat_box(title, value):
        return ft.Container(
            width=100, height=80,
            bgcolor=AppColors.BG, border_radius=10,
            padding=10,
            content=ft.Column([
                ft.Text(title, size=12, color=AppColors.TEXT_SUB),
                ft.Text(str(value), size=14, weight="bold", color=AppColors.TEXT_MAIN, overflow=ft.TextOverflow.ELLIPSIS)
            ], alignment="center", horizontal_alignment="center")
        )

    def run_analysis(code):
        nonlocal current_symbol
        symbol = StockService.format_symbol(code)
        if not symbol: return
        
        current_symbol = symbol
        txt_search.value = symbol
        
        # 重置 UI
        loading_indicator.visible = True
        tabs_content.visible = False
        lbl_detail_price.value = "載入中..."
        lbl_detail_change.value = ""
        btn_fav.disabled = True
        page.update()

        # 1. 獲取報價 (Quote)
        quote = StockService.get_quote(symbol)
        if not quote:
            lbl_detail_price.value = "查無資料"
            lbl_detail_change.value = "請確認代碼"
            loading_indicator.visible = False
            page.update()
            return

        # 更新頭部資訊
        lbl_detail_price.value = f"${quote['price']:.2f}"
        color = AppColors.UP if quote['change'] > 0 else AppColors.DOWN
        lbl_detail_change.value = f"{quote['change']:+.2f} ({quote['pct']:+.2f}%)"
        lbl_detail_change.color = color
        lbl_detail_price.color = color
        
        update_fav_icon(symbol)
        btn_fav.disabled = False
        page.update()

        # 2. 平行載入重型資料 (Chart, Details, News)
        # 為了保持 UI 響應，我們分步更新
        
        # A. K線圖
        b64 = StockService.generate_chart_image(symbol)
        if b64:
            chart_container.src_base64 = b64
            chart_container.visible = True
        
        # B. 基本面
        details = StockService.get_details(symbol)
        info_row.controls.clear()
        if details:
            # 格式化市值 (億)
            mkt_val = f"{details['mkt_cap']/100000000:.1f}億" if details['mkt_cap'] else "N/A"
            info_row.controls = [
                build_stat_box("最高", details['high']),
                build_stat_box("最低", details['low']),
                build_stat_box("成交量", details['volume']),
                build_stat_box("本益比", details['pe']),
                build_stat_box("EPS", details['eps']),
                build_stat_box("市值", mkt_val),
                ft.Container(width="100%", content=ft.Text(f"產業: {details['sector']}", color=AppColors.TEXT_SUB, size=12, text_align="center"))
            ]

        # C. 新聞
        news_items = StockService.get_news(symbol)
        news_col.controls.clear()
        if news_items:
            for n in news_items:
                # 轉換時間戳
                ts = n.get('providerPublishTime', 0)
                date_str = datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M')
                news_col.controls.append(
                    ft.Container(
                        padding=15, bgcolor=AppColors.BG, border_radius=10,
                        content=ft.Column([
                            ft.Text(n.get('title'), weight="bold", size=14, color=AppColors.TEXT_MAIN),
                            ft.Row([
                                ft.Text(n.get('publisher'), size=12, color=AppColors.PRIMARY),
                                ft.Text(date_str, size=12, color=AppColors.TEXT_SUB)
                            ], alignment="spaceBetween"),
                            # ft.Text("點擊閱讀全文 (需外部瀏覽器)", size=10, italic=True, color=AppColors.TEXT_SUB)
                        ]),
                        on_click=lambda e, link=n.get('link'): page.launch_url(link)
                    )
                )
        else:
            news_col.controls.append(ft.Text("暫無相關新聞", color=AppColors.TEXT_SUB))

        # 完成載入
        loading_indicator.visible = False
        tabs_content.visible = True
        page.update()

    def load_analysis_page(symbol):
        """從自選列表點擊跳轉"""
        page.navigation_bar.selected_index = 1
        page.update()
        
        # 切換視圖
        view_watchlist.visible = False
        view_analysis.visible = True
        
        # 執行搜尋
        run_analysis(symbol)

    # --- 導航控制 ---
    def on_nav_change(e):
        idx = e.control.selected_index
        if idx == 0:
            view_watchlist.visible = True
            view_analysis.visible = False
            refresh_watchlist()
        else:
            view_watchlist.visible = False
            view_analysis.visible = True
        page.update()

    page.navigation_bar = ft.NavigationBar(
        bgcolor=AppColors.SURFACE,
        indicator_color=AppColors.PRIMARY,
        destinations=[
            ft.NavigationDestination(icon=ft.icons.DASHBOARD_OUTLINED, selected_icon=ft.icons.DASHBOARD, label="監控"),
            ft.NavigationDestination(icon=ft.icons.ANALYTICS_OUTLINED, selected_icon=ft.icons.ANALYTICS, label="分析"),
        ],
        on_change=on_nav_change
    )

    # --- 啟動 ---
    page.add(ft.Stack([view_watchlist, view_analysis]))
    refresh_watchlist()

if __name__ == "__main__":
    ft.app(target=main)
