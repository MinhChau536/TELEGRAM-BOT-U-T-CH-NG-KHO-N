import requests
import pandas as pd
from datetime import datetime

BASE_URL = "https://api.example.com"  # Replace with the actual API endpoint

def fetch_prices(symbol: str, start_date: str, end_date: str, interval: str = "1D") -> pd.DataFrame:
    """Fetch historical stock prices for a given symbol."""
    url = f"{BASE_URL}/prices?symbol={symbol}&start={start_date}&end={end_date}&interval={interval}"
    response = requests.get(url)

    if response.status_code != 200:
        raise ValueError(f"Error fetching data for {symbol}: {response.text}")

    data = response.json()
    return pd.DataFrame(data)

def get_financials(symbol: str, period: str = "quarter", max_periods: int = 8) -> pd.DataFrame:
    """Fetch financial data for a given symbol."""
    url = f"{BASE_URL}/financials?symbol={symbol}&period={period}&max_periods={max_periods}"
    response = requests.get(url)

    if response.status_code != 200:
        raise ValueError(f"Error fetching financial data for {symbol}: {response.text}")

    data = response.json()
    return pd.DataFrame(data)

def format_date(date_str: str) -> str:
    """Convert date string to a standard format."""
    return datetime.strptime(date_str, "%Y-%m-%d").strftime("%d/%m/%Y")