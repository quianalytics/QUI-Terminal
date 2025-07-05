import cmd
import threading
import time
import sqlite3
import re
import yfinance as yf
import feedparser
from rich import print
from rich.table import Table
from rich.console import Console
import matplotlib.pyplot as plt
from plyer import notification
import ssl
import queue
from textblob import TextBlob
import requests
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
import utils as u
import os
from dotenv import load_dotenv
from fredapi import Fred
from playwright.sync_api import sync_playwright
import urllib.request

ALERT_CHECK_INTERVAL = 30  # seconds

ssl._create_default_https_context = ssl._create_unverified_context

load_dotenv()
FRED_API_KEY = os.getenv("FRED_API_KEY")
fred = Fred(api_key=FRED_API_KEY)

class QUITerminal(cmd.Cmd):
    intro = "Welcome to the QUI Terminal. Type help or ? to list commands.\n"
    prompt = "> "

    def __init__(self):
        super().__init__()
        self.alerts = {}  # ticker -> alert dict
        self.db = sqlite3.connect("alerts.db")
        self._setup_db()
        self._load_alerts()
        self._start_alert_threads()
        self.alert_queue = queue.Queue()
        self._setup_db()
        self._load_alerts()
        self._start_alert_listener()

    def _setup_db(self):
        c = self.db.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                ticker TEXT PRIMARY KEY,
                target_price REAL,
                direction TEXT
            )
        """)
        self.db.commit()

    def _load_alerts(self):
        c = self.db.cursor()
        for row in c.execute("SELECT ticker, target_price, direction FROM alerts"):
            ticker, target_price, direction = row
            self.alerts[ticker] = {
                "price": target_price,
                "direction": direction,
                "thread": None,
                "active": True
            }

    def _start_alert_threads(self):
        for ticker in list(self.alerts.keys()):
            self._start_alert_thread(ticker)

    def _start_alert_listener(self):
        def alert_listener():
            while True:
                ticker = self.alert_queue.get()  # blocking wait
                if ticker is None:
                    break  # exit signal for clean shutdown if needed
                self._remove_alert(ticker)
        threading.Thread(target=alert_listener, daemon=True).start()

    def _start_alert_thread(self, ticker):
        alert = self.alerts[ticker]
        def check_price():
            thread_db = sqlite3.connect("alerts.db")
            while alert["active"]:
                try:
                    stock = yf.Ticker(ticker)
                    current_price = stock.info.get("regularMarketPrice")
                    if current_price is None:
                        time.sleep(ALERT_CHECK_INTERVAL)
                        continue

                    if alert["direction"] == "above" and current_price >= alert["price"]:
                        notification.notify(
                            title=f"{ticker} Alert",
                            message=f"Price is above {alert['price']}: {current_price}",
                            timeout=10
                        )
                        print(f"[green]ALERT: {ticker} price is above {alert['price']} (Current: {current_price})[/green]")
                        self.alert_queue.put(ticker)  # notify main thread
                        break

                    elif alert["direction"] == "below" and current_price <= alert["price"]:
                        notification.notify(
                            title=f"{ticker} Alert",
                            message=f"Price is below {alert['price']}: {current_price}",
                            timeout=10
                        )
                        print(f"[green]ALERT: {ticker} price is below {alert['price']} (Current: {current_price})[/green]")
                        self.alert_queue.put(ticker)  # notify main thread
                        break
                except Exception as e:
                    print(f"[yellow]Warning: Alert check failed for {ticker}: {e}[/yellow]")
                time.sleep(ALERT_CHECK_INTERVAL)
            thread_db.close()
        
        thread = threading.Thread(target=check_price, daemon=True)
        alert["thread"] = thread
        thread.start()

    def _remove_alert(self, ticker):
        alert = self.alerts.get(ticker)
        if alert:
            alert["active"] = False
            self.alerts.pop(ticker)
            with sqlite3.connect("alerts.db") as conn:
                c = conn.cursor()
                c.execute("DELETE FROM alerts WHERE ticker=?", (ticker,))
                conn.commit()
            print(f"[cyan]Alert for {ticker} removed.[/cyan]")

    def do_alert(self, arg):
        """
        Set a price alert:
        alert TICKER PRICE DIRECTION
        DIRECTION: 'above' or 'below'
        Example:
        alert AAPL 150 above
        """
        parts = arg.split()
        if len(parts) != 3:
            print("[red]Usage: alert TICKER PRICE DIRECTION[/red]")
            return

        ticker, price_str, direction = parts
        ticker = ticker.upper()
        direction = direction.lower()
        if direction not in ("above", "below"):
            print("[red]Direction must be 'above' or 'below'[/red]")
            return

        try:
            target_price = float(price_str)
        except ValueError:
            print("[red]Price must be a number[/red]")
            return

        if ticker in self.alerts:
            print(f"[yellow]Alert for {ticker} already exists. Cancel it first.[/yellow]")
            return

        c = self.db.cursor()
        c.execute("INSERT OR REPLACE INTO alerts (ticker, target_price, direction) VALUES (?, ?, ?)",
                  (ticker, target_price, direction))
        self.db.commit()

        self.alerts[ticker] = {
            "price": target_price,
            "direction": direction,
            "thread": None,
            "active": True
        }
        self._start_alert_thread(ticker)
        print(f"[green]Alert set for {ticker} {direction} {target_price}[/green]")

    def do_alerts(self, arg):
        """List active alerts."""
        if not self.alerts:
            print("No active alerts.")
            return
        print("Active alerts:")
        for t, alert in self.alerts.items():
            print(f"  {t}: {alert['direction']} {alert['price']}")

    def do_cancel_alert(self, ticker):
        """Cancel alert for ticker."""
        ticker = ticker.strip().upper()
        if not ticker:
            print("[red]Usage: cancel_alert TICKER[/red]")
            return
        if ticker not in self.alerts:
            print(f"No active alert for {ticker}.")
            return
        self._remove_alert(ticker)
        print(f"[green]Alert for {ticker} canceled.[/green]")


    def do_quote(self, ticker):
        """Get the latest stock price: quote TICKER"""
        if not ticker:
            print("[red]Please provide a ticker symbol.[/red]")
            return
        stock = yf.Ticker(ticker)
        price = stock.info.get("regularMarketPrice")
        name = stock.info.get("shortName", "N/A")
        if price:
            print(f"[bold cyan]{name}[/bold cyan] ({ticker.upper()}): ${price:.2f}")
        else:
            print(f"[red]Could not retrieve data for {ticker.upper()}[/red]")

    def do_fundamentals(self, ticker):
        """Show key financial metrics and historical quarterly financials: fundamentals TICKER"""
        if not ticker:
            print("[red]Please provide a ticker symbol.[/red]")
            return
        try:
            stock = yf.Ticker(ticker)
            info = stock.info

            # Trailing 12 months (TTM) summary metrics
            fcf = info.get("freeCashflow")
            shares = info.get("sharesOutstanding")
            fcf_per_share = (fcf / shares) if (fcf and shares) else None

            table = Table(title=f"{ticker.upper()} Financials (TTM)")
            table.add_column("Metric")
            table.add_column("Value", justify="right")
            table.add_row("Revenue (TTM)", f"${info.get('totalRevenue', 'N/A'):,}" if info.get('totalRevenue') else "N/A")
            table.add_row("EBITDA", f"${info.get('ebitda', 'N/A'):,}" if info.get('ebitda') else "N/A")
            table.add_row("Free Cash Flow", f"${fcf:,}" if fcf else "N/A")
            table.add_row("FCF / Share", f"${fcf_per_share:.2f}" if isinstance(fcf_per_share, float) else "N/A")
            print(table)

            def format_and_print_df(df, title, rows_to_show):
                if df is None or df.empty:
                    print(f"[yellow]No {title} data available.[/yellow]")
                    return
                df = df.fillna("N/A")
                q_table = Table(title=f"{ticker.upper()} {title}")
                q_table.add_column("Metric")
                for date in df.columns:
                    q_table.add_column(str(date.date()))
                for metric in rows_to_show:
                    if metric in df.index:
                        values = [
                            f"${int(v):,}" if isinstance(v, (int, float)) and v != "N/A" else str(v)
                            for v in df.loc[metric]
                        ]
                        q_table.add_row(metric, *values)
                print(q_table)

            # Quarterly Income Statement
            q_financials = stock.quarterly_financials
            format_and_print_df(
                q_financials,
                "Quarterly Income Statement",
                ["Total Revenue", "EBITDA", "Net Income", "Operating Income"]
            )

            # Quarterly Cash Flow Statement
            q_cashflow = stock.quarterly_cashflow
            format_and_print_df(
                q_cashflow,
                "Quarterly Cash Flow Statement",
                ["Operating Cash Flow", "Capital Expenditures", "Free Cash Flow"]
            )

            # Quarterly Balance Sheet
            q_balancesheet = stock.quarterly_balance_sheet
            format_and_print_df(
                q_balancesheet,
                "Quarterly Balance Sheet",
                ["Total Assets", "Total Liab", "Total Stockholder Equity"]
            )

        except Exception as e:
            print(f"[red]Error retrieving financials: {e}[/red]")
                
    def do_news(self, ticker):
        """Show top 5 Google News headlines: news TICKER"""
        ssl._create_default_https_context = ssl._create_unverified_context
        if not ticker:
            print("[red]Please provide a ticker symbol.[/red]")
            return
        rss_url = f"https://news.google.com/rss/search?q={ticker}&hl=en-US&gl=US&ceid=US:en"
        feed = feedparser.parse(rss_url)
        entries = feed.entries
        if not entries:
            print(f"[yellow]No news found for {ticker.upper()}.[/yellow]")
            return
        print(f"[bold]Top News for {ticker.upper()}[/bold]\n")
        for entry in entries[:5]:
            print(f"- [blue]{entry.title}[/blue]")
            print(f"  [dim]{entry.link}[/dim]\n")
      
    def do_sentiment(self, ticker):
    # Show news headlines with sentiment analysis (TextBlob): sentiment TICKER
        if not ticker:
            print("[red]Please provide a ticker symbol.[/red]")
            return

        ssl._create_default_https_context = ssl._create_unverified_context
        rss_url = f"https://news.google.com/rss/search?q={ticker}&hl=en-US&gl=US&ceid=US:en"
        feed = feedparser.parse(rss_url)
        entries = feed.entries
        if not entries:
            print(f"[yellow]No news found for {ticker.upper()}.[/yellow]")
            return

        print(f"[bold]News Sentiment for {ticker.upper()}[/bold]\n")

        sentiments = []
        for entry in entries[:10]:
            headline = entry.title
            score = TextBlob(headline).sentiment.polarity
            sentiments.append(score)

            if score >= 0.1:
                color = "green"
                label = "Positive"
            elif score <= -0.1:
                color = "red"
                label = "Negative"
            else:
                color = "yellow"
                label = "Neutral"

            print(f"- [{color}]{headline}[/{color}] ({label}, score: {score:.2f})")
            print(f"  [dim]{entry.link}[/dim]\n")

        avg_score = sum(sentiments) / len(sentiments)
        print(f"[bold]Average Sentiment Score:[/bold] {avg_score:.2f}")

    def do_chart(self, arg):
        """
        Show closing price chart: chart TICKER [RANGE]
        RANGE options: 7d, 30d, 90d, 1y
        Example: chart AAPL 90d
        """
        args = arg.split()
        if len(args) == 0:
            print("Usage: chart TICKER [RANGE]")
            return
        ticker = args[0]
        if len(args) > 1:
            date_range = args[1].lower()
        else:
            date_range = "30d"

        match = re.match(r"(\d+)([dy])", date_range)
        if not match:
            print(f"[red]Invalid date range '{date_range}'. Use formats like 7d, 30d, 1y.[/red]")
            return

        num = int(match.group(1))
        unit = match.group(2)

        if unit == "d":
            period_days = num
        elif unit == "y":
            period_days = num * 365
        else:
            print(f"[red]Invalid unit '{unit}' in date range.[/red]")
            return

        data = yf.download(ticker, period=f"{period_days}d", interval="1d", progress=False)
        if data.empty or "Close" not in data.columns:
            print(f"[red]No price data found for {ticker.upper()}[/red]")
            return

        data = data[["Close"]].dropna()
        if data.empty:
            print(f"[red]No valid closing price data for {ticker.upper()}[/red]")
            return

        data['Date'] = data.index

        plt.figure(figsize=(10, 5))
        plt.plot(data['Date'], data['Close'], marker='o')
        plt.title(f"{ticker.upper()} - Last {period_days} Days Closing Prices")
        plt.xlabel("Date")
        plt.ylabel("Close Price ($)")
        plt.xticks(rotation=45)
        plt.tight_layout()
        plt.grid(True)
        plt.show()

    def do_earnings(self, ticker):
    # Show earnings calendar for a stock: earnings TICKER
        if not ticker:
            print("[red]Please provide a ticker symbol.[/red]")
            return
        try:
            stock = yf.Ticker(ticker)
            calendar = stock.calendar

            # Safely check if it's a DataFrame and has content
            if not hasattr(calendar, "empty") or calendar.empty:
                print(f"[yellow]No earnings calendar data found for {ticker.upper()}.[/yellow]")
                return

            print(f"[bold]Earnings Calendar for {ticker.upper()}[/bold]\n")
            for index, value in calendar.items():
                label = index.replace("_", " ").title()
                val = value[0] if isinstance(value, (list, tuple, pd.Series)) else value
                print(f"{label}: [cyan]{val}[/cyan]")

        except Exception as e:
            print(f"[red]Error fetching earnings data: {e}[/red]")

    def do_earnings_week(self, arg):
        """
        Show weekly earnings calendar from Nasdaq: earnings_week
        """
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
            "Referer": "https://www.nasdaq.com/"
        }

        today = datetime.today()
        for offset in range(7):
            day = today + timedelta(days=offset)
            date_str = day.strftime("%Y-%m-%d")
            url = f"https://api.nasdaq.com/api/calendar/earnings?date={date_str}"

            try:
                response = requests.get(url, headers=headers, timeout=10)
                data = response.json()

                rows = data.get("data", {}).get("rows", [])
                if not rows:
                    continue

                print(f"\n[bold cyan]{day.strftime('%A, %B %d, %Y')}[/bold cyan]")
                for row in rows:
                    symbol = row.get("symbol", "N/A")
                    name = row.get("company", "N/A")
                    time = row.get("time", "N/A")
                    eps_est = row.get("epsEstimate", "N/A")
                    print(f"- {symbol} | {name} | Time: {time} | EPS Est: {eps_est}")

            except Exception as e:
                print(f"[red]Error fetching data for {date_str}: {e}[/red]")

    def do_options(self, arg):
    # View options for a stock: options TICKER [EXPIRY] [calls|puts] Example: options AAPL -> shows available expiriesoptions AAPL 2024-07-19 calls  -> shows call options
        parts = arg.strip().split()
        if len(parts) == 0:
            print("Usage: options TICKER [EXPIRY] [calls|puts]")
            return

        ticker = parts[0].upper()
        try:
            stock = yf.Ticker(ticker)
            expirations = stock.options
            if len(parts) == 1:
                print(f"[bold]Available Option Expirations for {ticker}[/bold]:")
                for date in expirations:
                    print(f"- {date}")
                return

            if len(parts) >= 2:
                expiry = parts[1]
                if expiry not in expirations:
                    print(f"[red]Invalid expiration date. Use 'options {ticker}' to list valid dates.[/red]")
                    return

                chain = stock.option_chain(expiry)
                data = chain.calls if len(parts) < 3 or parts[2].lower() == "calls" else chain.puts
                print(f"[bold]Options for {ticker} on {expiry} ({'Calls' if data.equals(chain.calls) else 'Puts'})[/bold]")

                table = Table(show_lines=False)
                table.add_column("Strike", justify="right")
                table.add_column("Bid", justify="right")
                table.add_column("Ask", justify="right")
                table.add_column("Last Price", justify="right")
                table.add_column("Volume", justify="right")
                table.add_column("Open Interest", justify="right")

                for _, row in data.iterrows():
                    table.add_row(
                        f"{row['strike']:.2f}",
                        f"{row['bid']:.2f}",
                        f"{row['ask']:.2f}",
                        f"{row['lastPrice']:.2f}",
                        f"{int(row['volume'])}",
                        f"{int(row['openInterest'])}"
                    )
                print(table)

        except Exception as e:
            print(f"[red]Error retrieving options data: {e}[/red]")

    def do_market(self, arg):
    #Show global market summary: market

        tickers = {
               # US
                "^GSPC": "S&P 500 (US)",
                "^DJI": "Dow Jones (US)",
                "^IXIC": "Nasdaq (US)",
                "^RUT": "Russell 2000 (US)",
                "^VIX": "VIX (Volatility US)",
                "CL=F": "Crude Oil (US)",
                "GC=F": "Gold (US)",
                "BTC-USD": "Bitcoin",
                "ETH-USD": "Ethereum",
                "SOL-USD": "Solana (Crypto)",
                # Europe
                "^FTSE": "FTSE 100 (UK)",
                "^GDAXI": "DAX (Germany)",
                "^FCHI": "CAC 40 (France)",
                "^STOXX50E": "STOXX 50 (Europe)",
                # Asia
                "^N225": "Nikkei 225 (Japan)",
                "^HSI": "Hang Seng (Hong Kong)",
                "000001.SS": "Shanghai Composite (China)",
                "^AXJO": "S&P/ASX 200 (Australia)"
        }

        table = Table(title="Global Market Summary")
        table.add_column("Index")
        table.add_column("Price", justify="right")
        table.add_column("Change", justify="right")

        for symbol, name in tickers.items():
            try:
                stock = yf.Ticker(symbol)
                info = stock.info
                price = info.get("regularMarketPrice")
                change = info.get("regularMarketChangePercent")
                if price is None or change is None:
                    table.add_row(name, "N/A", "N/A")
                else:
                    color = "green" if change >= 0 else "red"
                    table.add_row(
                        name,
                        f"${price:,.2f}",
                        f"[{color}]{change:+.2f}%[/{color}]"
                    )
            except Exception as e:
                table.add_row(name, "Error", f"{e}")

        print(table)

    def do_forex_rates(self, arg):
        # Show major Forex rates: forex

        pairs = {
            "EUR/USD": "EURUSD=X",
            "USD/JPY": "JPY=X",
            "GBP/USD": "GBPUSD=X",
            "USD/CHF": "CHF=X",
            "AUD/USD": "AUDUSD=X",
            "USD/CAD": "CAD=X",
            "NZD/USD": "NZDUSD=X",
            "USD/MXN": "MXN=X",
            "USD/TRY": "TRY=X",
            "USD/ZAR": "ZAR=X",
            "EUR/GBP": "EURGBP=X",
            "EUR/JPY": "EURJPY=X",
            "GBP/JPY": "GBPJPY=X",
            "EUR/AUD": "EURAUD=X",
            "EUR/CAD": "EURCAD=X",
            "AUD/JPY": "AUDJPY=X",
            "CHF/JPY": "CHFJPY=X",
            "GBP/CAD": "GBPCAD=X",
            "NZD/JPY": "NZDJPY=X",
            "USD/SGD": "SGD=X",
            "USD/HKD": "HKD=X",
            "USD/NOK": "NOK=X",
            "USD/SEK": "SEK=X",
            "USD/DKK": "DKK=X",
            "USD/PLN": "PLN=X",
        }


        table = Table(title="Major Forex Rates")
        table.add_column("Pair")
        table.add_column("Price", justify="right")
        table.add_column("Change", justify="right")

        for name, ticker in pairs.items():
            try:
                fx = yf.Ticker(ticker)
                info = fx.info
                price = info.get("regularMarketPrice")
                change = info.get("regularMarketChangePercent")
                if price is None or change is None:
                    table.add_row(name, "N/A", "N/A")
                else:
                    color = "green" if change >= 0 else "red"
                    table.add_row(
                        name,
                        f"{price:.4f}",
                        f"[{color}]{change:+.2%}[/{color}]"
                    )
            except Exception as e:
                table.add_row(name, "Error", str(e))

        print(table)

    def do_insider(self, arg):
        """
        Show recent insider trades from Finviz: insider TICKER
        """
        def get_insider_trades_playwright(ticker):
            url = f"https://finviz.com/insidertrading.ashx?t={ticker}"

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox"
                ])
                page = browser.new_page()
                page.set_extra_http_headers({
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
                })

                page.evaluate("""() => {
                    Object.defineProperty(navigator, 'webdriver', { get: () => false });
                    window.navigator.chrome = { runtime: {} };
                    Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
                    Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
                }""")

                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_selector("#insider-table")
                    rows = page.query_selector_all("#insider-table > tbody > tr")
                    trades = []
                    for row in rows:
                        cells = row.query_selector_all("td")
                        if len(cells) >= 6:
                            insider = cells[1].inner_text().strip()
                            relation = cells[2].inner_text().strip()
                            date = cells[3].inner_text().strip()
                            txn = cells[4].inner_text().strip()
                            shares = cells[6].inner_text().strip()
                            price = cells[5].inner_text().strip()
                            total_value = cells[7].inner_text().strip()
                            trades.append((insider, relation, date, txn, shares, price, total_value))

                    browser.close()
                    return trades

                except Exception as e:
                    html = page.content()
                    with open("insider_debug.html", "w", encoding="utf-8") as f:
                        f.write(html)
                    browser.close()
                    raise Exception(f"Error fetching insider data for {ticker}: {e}")

        if not arg:
            print("[red]Please provide a stock ticker symbol.[/red]")
            return
        ticker = arg.upper()
        try:
            trades = get_insider_trades_playwright(ticker)
            table = Table(title=f"Recent Insider Trades for {ticker}")
            table.add_column("Insider", style="cyan")
            table.add_column("Relation")
            table.add_column("Date", style="green")
            table.add_column("Transaction", style="yellow")
            table.add_column("Shares", justify="right")
            table.add_column("Price", justify="right")
            table.add_column("Total Value", justify="right")

            for trade in trades:
                table.add_row(*trade)

            print(table)
        except Exception as e:
            print(f"[red]{e}[/red]")

    def do_econ_calendar(self, arg):
        """ Show upcoming economic events for the next 3 days: econ_calendar """
        url = "https://www.investing.com/economic-calendar/"
        headers = {"User-Agent": "Mozilla/5.0"}

        try:
            response = requests.get(url, headers=headers)
            soup = BeautifulSoup(response.text, "html.parser")

            # The calendar is dynamic; but Investing.com provides a table with event rows
            table = soup.find("table", id="economicCalendarData")
            if not table:
                print("[red]Failed to find economic calendar table.[/red]")
                return

            print("[bold]Upcoming Economic Events (Next 3 Days):[/bold]\n")

            today = datetime.today()
            end_date = today + timedelta(days=3)

            rows = table.find_all("tr", attrs={"data-event-datetime": True})

            count = 0
            for row in rows:
                date_str = row["data-event-datetime"]
                event_date = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
                if event_date.date() > end_date.date():
                    continue
                if event_date.date() < today.date():
                    continue

                time = event_date.strftime("%Y-%m-%d %H:%M")
                currency = row.find("td", class_="flagCur").get_text(strip=True)
                event = row.find("td", class_="event").get_text(strip=True)
                impact_td = row.find("td", class_="sentiment")
                impact = impact_td.find("span")["title"] if impact_td and impact_td.find("span") else "N/A"
                actual = row.find("td", class_="actual").get_text(strip=True)
                forecast = row.find("td", class_="forecast").get_text(strip=True)
                previous = row.find("td", class_="previous").get_text(strip=True)

                print(f"{time} | {currency} | {impact} impact | {event}")
                print(f"  Actual: {actual} | Forecast: {forecast} | Previous: {previous}\n")
                count += 1
                if count >= 20:  # Limit output to 20 events
                    break

            if count == 0:
                print("[yellow]No economic events found for the next 3 days.[/yellow]")

        except Exception as e:
            print(f"[red]Error fetching economic calendar: {e}[/red]")
    
    def do_company_info(self, ticker):
        """Show company info: company_info TICKER"""
        if not ticker:
            print("[red]Please provide a ticker symbol.[/red]")
            return
        try:
            stock = yf.Ticker(ticker)
            info = stock.info

            table = Table(title=f"{ticker.upper()} Company Info")

            def safe_get(key):
                return info.get(key, "N/A") if info else "N/A"

            table.add_column("Field")
            table.add_column("Value", overflow="fold")

            table.add_row("Name", safe_get("longName"))
            table.add_row("Sector", safe_get("sector"))
            table.add_row("Industry", safe_get("industry"))
            table.add_row("CEO", safe_get("companyOfficers")[0]["name"] if safe_get("companyOfficers") else "N/A")
            table.add_row("Website", safe_get("website"))
            table.add_row("Headquarters", safe_get("city") + ", " + safe_get("state") if safe_get("city") and safe_get("state") else "N/A")
            table.add_row("Description", safe_get("longBusinessSummary")[:500] + "..." if safe_get("longBusinessSummary") else "N/A")

            print(table)

        except Exception as e:
            print(f"[red]Error fetching company info: {e}[/red]")

    def do_glossary(self, arg):
        """
        Financial glossary: glossary [term] or glossary category:[category_name]
        Examples:
            glossary                       -> lists all terms
            glossary volatility            -> shows definition of a term
            glossary category:technical    -> lists technical analysis terms
        """
        
        console = Console()
        arg = arg.strip().lower()

        if not arg:
            table = Table(title="Financial Glossary Terms")
            table.add_column("Term")
            table.add_column("Definition")
            for category, terms in u.FINANCE_GLOSSARY.items():
                for term, definition in terms.items():
                    table.add_row(term.title(), definition)
            console.print(table)

        elif arg.startswith("category:"):
            category = arg.split("category:")[1].strip()
            if category not in u.FINANCE_GLOSSARY:
                console.print(f"[red]No such category: {category}[/red]")
                console.print(f"Available categories: {', '.join(u.FINANCE_GLOSSARY.keys())}")
                return
            terms = u.FINANCE_GLOSSARY[category]
            table = Table(title=f"Glossary Category: {category.title()}")
            table.add_column("Term")
            table.add_column("Definition")
            for term, definition in terms.items():
                table.add_row(term.title(), definition)
            console.print(table)

        else:
            found = []
            for category, terms in u.FINANCE_GLOSSARY.items():
                for term, definition in terms.items():
                    if arg in term.lower():
                        found.append((term, definition, category))
            if not found:
                console.print(f"[red]No matching term found for '{arg}'[/red]")
            else:
                table = Table(title=f"Glossary Search Results for '{arg}'")
                table.add_column("Term")
                table.add_column("Definition")
                table.add_column("Category")
                for term, definition, category in found:
                    table.add_row(term.title(), definition, category.title())
                console.print(table)

    def do_etf_holdings(self, arg):
        def get_etf_holdings_playwright(ticker):
            url = f"https://etfdb.com/etf/{ticker}/#holdings"

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox"
                ])
                page = browser.new_page()
                page.set_extra_http_headers({
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
                })

                page.evaluate("""() => {
                    Object.defineProperty(navigator, 'webdriver', { get: () => false });
                    window.navigator.chrome = { runtime: {} };
                    Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
                    Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
                }""")

                try:
                    # Load page and wait until just DOM is ready
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_selector("#etf-holdings")
                    rows = page.query_selector_all("#etf-holdings > tbody > tr")
                    holdings = []
                    for row in rows[:10]:
                        cells = row.query_selector_all("td")
                        ticker_text = cells[0].inner_text().strip()
                        name_text = cells[1].inner_text().strip()
                        weight_text = cells[2].inner_text().strip().replace('%','')
                        holdings.append((ticker_text, name_text, weight_text))
                    
                    browser.close()
                    return holdings

                except Exception as e:
                    html = page.content()
                    with open("etf_debug.html", "w", encoding="utf-8") as f:
                        f.write(html)
                    browser.close()
                    raise Exception(f"Error fetching ETF holdings for {ticker}: {e}")

        if not arg:
            print("[red]Please provide an ETF ticker symbol.[/red]")
            return
        etf_ticker = arg.upper()
        try:
            holdings = get_etf_holdings_playwright(etf_ticker)
            table = Table(title=f"Top Holdings of {etf_ticker}")
            table.add_column("Ticker", style="cyan")
            table.add_column("Name")
            table.add_column("Weight (%)", justify="right")

            for ticker, name, weight in holdings:
                table.add_row(ticker, name, weight)

            print(table)
        except Exception as e:
            print(f"[red]Error fetching ETF holdings for {etf_ticker}: {e}[/red]")

    def do_exit(self, arg):
        """Exit the terminal."""
        print("Goodbye!")
        self.alert_queue.put(None)
        return True
    
    def do_macro_dashboard(self, arg):
        """
        Show U.S. macroeconomic dashboard with grouped categories: do_macro
        """
        console = Console()

        grouped_indicators = {
            "Inflation": {
                "CPI (YoY)": "CPIAUCSL",
                "Core CPI (YoY)": "CPILFESL",
                "PCE Inflation": "PCEPI",
            },
            "Labor Market": {
                "Unemployment Rate": "UNRATE",
                "Labor Force Participation": "CIVPART",
                "Nonfarm Payrolls": "PAYEMS",
            },
            "GDP & Growth": {
                "GDP Growth (QoQ %)": "A191RL1Q225SBEA",
            },
            "Monetary Policy & Rates": {
                "Fed Funds Rate": "FEDFUNDS",
                "10Y Treasury Yield": "GS10",
                "2Y Treasury Yield": "GS2",
                "M2 Money Supply": "M2SL",
            },
            "Consumer & Retail": {
                "Consumer Confidence": "UMCSENT",
                "Retail Sales (YoY)": "RSAFS",
            },
            "Manufacturing & Housing": {
                "ISM Manufacturing PMI": "NAPM",
                "Case-Shiller Home Price Index": "CSUSHPINSA",
                "Housing Starts": "HOUST",
            },
            "Commodities": {
                "Crude Oil Price (WTI)": "DCOILWTICO",
                "Gold Price": "GOLDAMGBD228NLBM",
            }
        }

        for category, indicators in grouped_indicators.items():
            table = Table(title=f"{category} Indicators")
            table.add_column("Indicator")
            table.add_column("Value", justify="right")
            table.add_column("Date", justify="center")

            try:
                for label, series_id in indicators.items():
                    data = fred.get_series(series_id)
                    latest_date = data.last_valid_index()
                    latest_value = data[latest_date]
                    value_str = f"{latest_value:.2f}" if isinstance(latest_value, (int, float)) else str(latest_value)
                    date_str = latest_date.strftime("%Y-%m-%d") if isinstance(latest_date, datetime) else str(latest_date)
                    table.add_row(label, value_str, date_str)
            except Exception as e:
                console.print(f"[red]Error fetching {category} data: {e}[/red]")

            console.print(table)
            console.print("\n")  # extra space between tables

    def do_sector_heatmap(self, arg):
        """
        Show a Market Heatmap of major sectors.
        Usage: heatmap
        """
        console = Console()

        sectors = {
            "Technology": "XLK",
            "Healthcare": "XLV",
            "Financials": "XLF",
            "Consumer Discretionary": "XLY",
            "Consumer Staples": "XLP",
            "Energy": "XLE",
            "Industrials": "XLI",
            "Materials": "XLB",
            "Real Estate": "XLRE",
            "Utilities": "XLU",
            "Communication Services": "XLC",
        }

        table = Table(title="Market Sector Heatmap (Daily % Change)", show_lines=False)
        table.add_column("Sector", style="bold")
        table.add_column("ETF", justify="center")
        table.add_column("Price", justify="right")
        table.add_column("Change %", justify="right")

        for sector, etf in sectors.items():
            try:
                stock = yf.Ticker(etf)
                hist = stock.history(period="5d", interval="1d")
                closes = hist["Close"].dropna()

                if len(closes) < 2:
                    raise ValueError("Not enough data")

                prev_close = closes.iloc[-2]
                last_close = closes.iloc[-1]
                change_pct = (last_close - prev_close) / prev_close

                color = "green" if change_pct >= 0 else "red"
                table.add_row(
                    sector,
                    etf,
                    f"${last_close:.2f}",
                    f"[{color}]{change_pct:+.2%}[/{color}]"
                )
            except Exception as e:
                table.add_row(sector, etf, "Error", "-")

        console.print(table)

    def do_sec_filings(self, ticker):
        """Show recent SEC filings: sec TICKER"""
        if not ticker:
            print("[red]Please provide a ticker symbol.[/red]")
            return

        rss_url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={ticker}&type=&count=10&output=atom"

        print(f"[bold]Recent SEC Filings for {ticker.upper()}[/bold]\n")

        try:
            headers = {
                "User-Agent": "MyCompanyName Contact: myemail@example.com"
            }
            request = urllib.request.Request(rss_url, headers=headers)
            with urllib.request.urlopen(request) as response:
                feed_data = response.read()

            feed = feedparser.parse(feed_data)

            if not feed.entries:
                print(f"[yellow]No filings found for {ticker.upper()}.[/yellow]")
                return

            for entry in feed.entries[:10]:
                title = entry.get("title", "No Title")
                updated = entry.get("updated", "")
                date = updated.split("T")[0] if "T" in updated else updated
                link = entry.get("link", "No Link")
                print(f"- [blue]{title}[/blue] ({date})")
                print(f"  [dim]{link}[/dim]\n")

        except Exception as e:
            print(f"[red]Error fetching SEC filings for {ticker.upper()}: {e}[/red]")




    
    def do_help(self, arg):
        print("[bold]Available Commands:[/bold]\n")
        print("- glossary [TERM]: View glossary of trading/finance terms or search by keyword")
        print("- market: Show real-time global index and commodity summary")
        print("- macro_dashboard: Show U.S. macroeconomic dashboard with key grouped indicators")
        print("- sector_heatmap: Show sector market heatmap based on ETF performance")
        print("- quote TICKER: Get the latest stock price")
        print("- etf_holdings TICKER: Show top holdings of an ETF")
        print("- fundamentals TICKER: Show revenue, EBITDA, and FCF per share")
        print("- company_info TICKER: Show company profile and details")
        print("- forex_rates: Show major Forex rates")
        print("- insider TICKER: Show recent insider trading activity for a ticker")
        print("- news TICKER: Show news headlines")
        print("- sentiment TICKER: Get sentiment score based on recent news headlines")
        print("- chart TICKER [RANGE]: Show closing price chart with optional range (7d,30d,90d,1y)")
        print("- earnings TICKER: Show upcoming earnings dates and events")
        print("- econ_calendar: Show upcoming economic events for the next 3 days")
        print("- earnings_week: Show all tickers reporting earnings in the next 7 days")
        print("- options TICKER [EXPIRY] [calls|puts]: View option chain for a given date")
        print("- alert TICKER PRICE DIRECTION: Set price alert (direction: above/below)")
        print("- alerts: List active alerts")
        print("- cancel_alert TICKER: Cancel alert for ticker")
        print("- exit: Quit the terminal")

if __name__ == "__main__":
    QUITerminal().cmdloop()
