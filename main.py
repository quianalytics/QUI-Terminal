import cmd
import yfinance as yf
import sqlite3
import feedparser
import plotext as plt
from rich import print
from rich.table import Table
import urllib.parse
import ssl


# --- Database Setup ---
conn = sqlite3.connect("watchlist.db")
cursor = conn.cursor()
cursor.execute("CREATE TABLE IF NOT EXISTS watchlist (ticker TEXT PRIMARY KEY)")
conn.commit()

class MiniTerminal(cmd.Cmd):
    intro = "Welcome to the QUI Terminal. Type help or ? to list commands.\n"
    prompt = "> "

    # --- QUOTE ---
    def do_quote(self, ticker):
        """Get the latest stock price: quote TICKER"""
        if not ticker:
            print("[red]Please provide a ticker.[/red]")
            return
        stock = yf.Ticker(ticker)
        price = stock.info.get("regularMarketPrice")
        name = stock.info.get("shortName", "N/A")
        if price:
            print(f"[bold cyan]{name}[/bold cyan] ({ticker.upper()}): ${price:.2f}")
        else:
            print(f"[red]Could not retrieve data for {ticker.upper()}[/red]")

    # --- CHART ---
    def do_chart(self, ticker):
        """Show last 30 days of closing prices: chart TICKER"""
        if not ticker:
            print("[red]Please provide a ticker.[/red]")
            return
        data = yf.download(ticker, period="30d")
        if data.empty:
            print(f"[red]No data found for {ticker}[/red]")
            return
        plt.clear_figure()
        plt.plot(data.index.strftime('%m-%d'), data["Close"], label=ticker)
        plt.title(f"{ticker.upper()} - 30 Day Closing Price")
        plt.show()

    # --- WATCHLIST ---
    def do_watchlist(self, arg):
        """Manage watchlist: watchlist add TICKER | remove TICKER | show"""
        args = arg.split()
        if not args:
            print("[red]Usage: watchlist add|remove|show TICKER[/red]")
            return
        cmd = args[0]
        if cmd == "add" and len(args) == 2:
            ticker = args[1].upper()
            try:
                cursor.execute("INSERT INTO watchlist (ticker) VALUES (?)", (ticker,))
                conn.commit()
                print(f"[green]{ticker} added to watchlist.[/green]")
            except sqlite3.IntegrityError:
                print(f"[yellow]{ticker} is already in the watchlist.[/yellow]")
        elif cmd == "remove" and len(args) == 2:
            ticker = args[1].upper()
            cursor.execute("DELETE FROM watchlist WHERE ticker = ?", (ticker,))
            conn.commit()
            print(f"[red]{ticker} removed from watchlist.[/red]")
        elif cmd == "show":
            cursor.execute("SELECT ticker FROM watchlist")
            rows = cursor.fetchall()
            if not rows:
                print("[yellow]Watchlist is empty.[/yellow]")
                return
            table = Table(title="Watchlist")
            table.add_column("Ticker")
            table.add_column("Price", justify="right")
            for (ticker,) in rows:
                stock = yf.Ticker(ticker)
                price = stock.info.get("regularMarketPrice", "N/A")
                table.add_row(ticker, f"${price:.2f}" if isinstance(price, float) else "N/A")
            print(table)
        else:
            print("[red]Invalid watchlist command. Use: add|remove|show[/red]")

    # --- FINANCIALS ---
    def do_fundamentals(self, ticker):
        """Show key financial metrics: fundamentals TICKER"""
        if not ticker:
            print("[red]Please provide a ticker.[/red]")
            return
        stock = yf.Ticker(ticker)
        try:
            info = stock.info
            fcf = info.get("freeCashflow", None)
            shares = info.get("sharesOutstanding", None)
            fcf_per_share = (fcf / shares) if (fcf and shares) else "N/A"
            table = Table(title=f"{ticker.upper()} Financials")
            table.add_column("Metric")
            table.add_column("Value", justify="right")
            table.add_row("Revenue (TTM)", f"${info.get('totalRevenue', 'N/A'):,}")
            table.add_row("EBITDA", f"${info.get('ebitda', 'N/A'):,}")
            table.add_row("Free Cash Flow", f"${fcf:,}" if fcf else "N/A")
            table.add_row("FCF / Share", f"${fcf_per_share:.2f}" if isinstance(fcf_per_share, float) else "N/A")
            print(table)
        except Exception as e:
            print(f"[red]Error retrieving financials: {e}[/red]")

    # --- NEWS ---
    def do_news(self, ticker):
        ssl._create_default_https_context = ssl._create_unverified_context
        """Show top 5 news headlines: news TICKER"""
        if not ticker:
            print("[red]Please provide a ticker.[/red]")
            return
        rss_url = f"https://news.google.com/rss/search?q={ticker}+stock&hl=en-US&gl=US&ceid=US:en"
        try:
            feed = feedparser.parse(rss_url)
            entries = feed.entries
            if not entries:
                print(f"[yellow]No news found for {ticker.upper()}.[/yellow]")
                return
            print(f"[bold]Top News for {ticker.upper()}[/bold]\n")
            for entry in entries[:5]:
                title = entry.title if hasattr(entry, 'title') else "No title"
                link = entry.link if hasattr(entry, 'link') else "No link"
                print(f"- [blue]{title}[/blue]")
                print(f"  [dim]{link}[/dim]\n")
        except Exception as e:
            print(f"[red]Error fetching news: {e}[/red]")
    
    # --- EXIT ---
    def do_exit(self, arg):
        """Exit the terminal."""
        print("Goodbye!")
        return True

    def do_help(self, arg):
        print("[bold]Available Commands:[/bold]\n")
        print("- quote TICKER: Get latest stock price")
        print("- chart TICKER: 30-day closing price chart")
        print("- fundamentals TICKER: Revenue, EBITDA, FCF/share")
        print("- news TICKER: Top recent headlines")
        print("- watchlist add|remove|show TICKER")
        print("- exit: Quit the terminal")


if __name__ == "__main__":
    MiniTerminal().cmdloop()
