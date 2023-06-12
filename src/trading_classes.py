import os
import pandas as pd
import yfinance as yf
import alpaca_trade_api as tradeapi
import configparser
import pytz
import locale
import pandas_market_calendars as mcal

from alpaca_trade_api.rest import APIError
from ta.volatility import BollingerBands
from ta.momentum import RSIIndicator
from ta.trend import SMAIndicator
from tqdm import tqdm
from requests_html import HTMLSession
from datetime import datetime


class TradingOpportunities:
    def __init__(self, n_stocks=25):
        """
        Description:
        Grabs top stock losers from YahooFinance! to determine trading opportunities using simple technical trading indicators
        such as Bollinger Bands and RSI.

        Arguments:
            •  n_stocks: number of top losing stocks that'll be pulled from YahooFinance! and considered in the algo

        Methods:
            • raw_get_daily_info(): Grabs a provided site and transforms HTML to a pandas df
            • get_trading_opportunities(): Grabs df from raw_get_daily_info() and provides just the top "n" losers declared by user in n_stocks
            • get_asset_info(): a df can be provided to specify which assets you'd like info for since this method is used in the Alpaca class. If no df argument is passed then tickers from get_trading_opportunities() method are used.
        """

        self.n_stocks = n_stocks

    def raw_get_daily_info(self, site):
        """
        Description:
        Grabs a provided site and transforms HTML to a pandas df

        Argument(s):
            • site: YahooFinance! top losers website provided in the get_day_losers() function below

        Other Notes:
        Commented out the conversion of market cap and volume from string to float since this threw an error.
        Can grab this from the yfinance API if needed or come back to this function and fix later.
        """

        session = HTMLSession()
        response = session.get(site)

        tables = pd.read_html(response.html.raw_html)
        df = tables[0].copy()
        df.columns = tables[0].columns

        session.close()
        return df

    def get_trading_opportunities(self):
        """
        Description:
        Grabs df from raw_get_daily_info() and provides just the top "n" losers declared by user in n_stocks

        Argument(s):
            • n_stocks: Number of top losers to analyze per YahooFinance! top losers site.
        """
        #####################
        #####################
        # Stock part
        df_stock = self.raw_get_daily_info(
            "https://finance.yahoo.com/losers?offset=0&count=100"
        )
        df_stock["asset_type"] = "stock"

        df_stock = df_stock.head(self.n_stocks)

        return df_stock

    def get_asset_info(self, df=None):
        """
        Description:
        Grabs historical prices for assets, calculates RSI and Bollinger Bands tech signals, and returns a df with all this data for the assets meeting the buy criteria.

        Argument(s):
            • df: a df can be provided to specify which assets you'd like info for since this method is used in the Alpaca class. If no df argument is passed then tickers from get_trading_opportunities() method are used.
        """

        # Grab technical stock info:
        if df is None:
            all_tickers = self.get_trading_opportunities()['Symbol']
        else:
            all_tickers = df['Symbol']
            
        technicals_dict = {}
        for ticker in tqdm(all_tickers):
            try:
                ticker = ticker.strip().split("/")[0]
                stock_df = yf.download(ticker, period='7d')

                # Calculate RSI
                rsi = RSIIndicator(stock_df['Close']).rsi()

                # Calculate Bollinger Bands
                bb = BollingerBands(stock_df['Close'])
                bb_width = bb.bollinger_pband() * 100  # Bollinger %b
                bb_low = bb.bollinger_lband()
                bb_high = bb.bollinger_hband()

                # Combine technical indicators into a single DataFrame
                tech_indicators = pd.DataFrame(
                    {'RSI': rsi, 'BB_Width': bb_width, 'BB_Low': bb_low, 'BB_High': bb_high}
                )
                tech_indicators = tech_indicators.dropna()

                technicals_dict[ticker] = tech_indicators.tail(1)

            except Exception as e:
                print(f"Error retrieving data for {ticker}: {str(e)}")

        # Combine all technical data into a single DataFrame
        df_technicals = pd.concat(technicals_dict).reset_index(level=0).rename(columns={'level_0': 'Symbol'})

        return df_technicals


class Alpaca:
    def __init__(self):
        """
        Description:
        Initializes Alpaca object and pulls in secret keys
        """

        # Load API keys from a config file
        config = configparser.ConfigParser()
        config.read(os.path.expanduser("~/.alpaca/credentials"))

        self.api_key = config.get("APCA-API-KEY-ID", "key_id")
        self.api_secret = config.get("APCA-API-SECRET-KEY", "secret_key")
        self.base_url = "https://paper-api.alpaca.markets"  # Paper-trading base URL

        self.api = tradeapi.REST(self.api_key, self.api_secret, self.base_url)

        # Get account information
        self.account = self.api.get_account()

        # Store available cash for trading
        self.cash = float(self.account.buying_power)

        # Get current positions
        self.df_current_positions = self.get_current_positions()

    def get_current_positions(self):
        """
        Description:
        Get the current positions from Alpaca account

        Returns:
        A DataFrame containing the current positions
        """

        positions = self.api.list_positions()
        positions_data = []

        for position in positions:
            positions_data.append({
                'Symbol': position.symbol,
                'Qty': int(position.qty),
                'Value': float(position.market_value),
                'Avg Entry': float(position.avg_entry_price),
                'Current Price': float(position.current_price),
                'Unrealized P/L': float(position.unrealized_pl)
            })

        df_positions = pd.DataFrame(positions_data)
        return df_positions

    def sell_order(self):
        """
        Description:
        Place sell orders for all stocks in the current positions

        Returns:
        A list of order IDs for the placed sell orders
        """
        sell_order_ids = []

        for _, position in self.df_current_positions.iterrows():
            try:
                symbol = position['Symbol']
                qty = position['Qty']
                order = self.api.submit_order(
                    symbol=symbol,
                    qty=qty,
                    side='sell',
                    type='market',
                    time_in_force='gtc'
                )
                sell_order_ids.append(order.id)

            except APIError as e:
                print(f"Error placing sell order for {symbol}: {e}")

        return sell_order_ids

    def buy_order(self, symbol):
        """
        Description:
        Place a buy order for a given stock symbol

        Argument(s):
            • symbol: Stock symbol to place the buy order for

        Returns:
        The order ID for the placed buy order, or None if the order was not placed
        """
        qty = int(self.cash / self.current_price)  # Calculate the quantity of shares to buy with the available cash

        try:
            order = self.api.submit_order(
                symbol=symbol,
                qty=qty,
                side='buy',
                type='market',
                time_in_force='gtc'
            )
            return order.id

        except APIError as e:
            print(f"Error placing buy order for {symbol}: {e}")
            return None


# Main program
if __name__ == '__main__':
    # Initialize trading opportunities object
    trading_opportunities = TradingOpportunities(n_stocks=1)

    # Get trading opportunities (top losing stocks)
    df_trading_opportunities = trading_opportunities.get_trading_opportunities()

    # Initialize Alpaca object
    alpaca = Alpaca()

    # Sell all existing positions
    alpaca.sell_order()

    # Buy CVNA stock
    symbol = 'CVNA'
    buy_order_id = alpaca.buy_order(symbol)

    if buy_order_id:
        print(f"Buy order placed for {symbol} (order ID: {buy_order_id})")
    else:
        print(f"Buy order for {symbol} was not placed")

    # Print current positions
    print("Current Positions:")
    print(alpaca.get_current_positions())
