import cmd
import threading
import time
import sqlite3
import re
import yfinance as yf
import feedparser
from rich import print
from rich.table import Table
import matplotlib.pyplot as plt
from plyer import notification
import ssl
import queue
from textblob import TextBlob

ALERT_CHECK_INTERVAL = 30  # seconds

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

    def do_exit(self, arg):
        """Exit the terminal."""
        print("Goodbye!")
        self.alert_queue.put(None)
        return True

    def do_help(self, arg):
        print("[bold]Available Commands:[/bold]\n")
        print("- quote TICKER: Get the latest stock price")
        print("- fundamentals TICKER: Show revenue, EBITDA, and FCF per share")
        print("- news TICKER: Show news headlines")
        print("- sentiment TICKER: Get sentiment score based on recent news headlines")
        print("- chart TICKER [RANGE]: Show closing price chart with optional range (7d,30d,90d,1y)")
        print("- alert TICKER PRICE DIRECTION: Set price alert (direction: above/below)")
        print("- alerts: List active alerts")
        print("- cancel_alert TICKER: Cancel alert for ticker")
        print("- exit: Quit the terminal")

if __name__ == "__main__":
    QUITerminal().cmdloop()

