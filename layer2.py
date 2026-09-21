import sqlite3
import numpy as np
import pandas as pd

from vnstock import Vnstock


DB_PATH = r"C:\Users\HP\Tang2_3_Strategy\output\market_data.db"


def percentile_rank(s):
    """
    Tính RS Percentile theo cross-section tại từng ngày.
    Kết quả từ 0 đến 100.
    """
    n = s.notna().sum()

    if n <= 1:
        return pd.Series(np.nan, index=s.index)

    return (s.rank(method="average") - 1) / (n - 1) * 100


def get_market_regime():
    """
    Lấy VN-Index và tính SMA200.

    VN-Index > SMA200  → Bullish
    VN-Index <= SMA200 → Non-Bullish
    """

    vn = Vnstock().stock(
        symbol="VNINDEX",
        source="KBS"
    )

    vnindex = vn.quote.history(
        start="2024-01-01",
        end=pd.Timestamp.today().strftime("%Y-%m-%d"),
        interval="1D"
    )

    if vnindex.empty:
        raise ValueError("Không lấy được dữ liệu VN-Index.")

    vnindex["time"] = pd.to_datetime(vnindex["time"])

    vnindex = vnindex.sort_values("time")

    vnindex["sma200"] = (
        vnindex["close"]
        .rolling(200)
        .mean()
    )

    latest_vn = vnindex.iloc[-1]

    vn_close = latest_vn["close"]
    vn_sma200 = latest_vn["sma200"]

    if pd.isna(vn_sma200):
        raise ValueError("Chưa đủ dữ liệu để tính SMA200.")

    if vn_close > vn_sma200:
        market_regime = "Bullish"
    else:
        market_regime = "Non-Bullish"

    return vn_close, vn_sma200, market_regime


def run_layer2():

    # ==================================================
    # 1. ĐỌC DỮ LIỆU DAILY
    # ==================================================

    conn = sqlite3.connect(DB_PATH)

    df = pd.read_sql_query(
        "SELECT * FROM prices WHERE resolution = '1D'",
        conn
    )

    conn.close()

    if df.empty:
        raise ValueError(
            "Không có dữ liệu Daily trong market_data.db"
        )

    df["time"] = pd.to_datetime(df["time"])

    df = (
        df
        .sort_values(["symbol", "time"])
        .reset_index(drop=True)
    )

    # ==================================================
    # 2. DAILY RETURN 6 THÁNG
    # ==================================================

    df["close_6m_ago"] = (
        df.groupby("symbol")["close"]
        .shift(126)
    )

    df["return_6m"] = (
        df["close"] / df["close_6m_ago"] - 1
    )

    # ==================================================
    # 3. RELATIVE STRENGTH PERCENTILE
    # ==================================================

    df["rs_percentile"] = (
        df.groupby("time")["return_6m"]
        .transform(percentile_rank)
    )

    # ==================================================
    # 4. LẤY PHIÊN GẦN NHẤT
    # ==================================================

    latest = (
        df
        .sort_values("time")
        .groupby("symbol")
        .tail(1)
        .copy()
    )

    # ==================================================
    # 5. MARKET REGIME
    # ==================================================

    vn_close, vn_sma200, market_regime = (
        get_market_regime()
    )

    latest["market_regime"] = market_regime
    latest["vn_index"] = vn_close
    latest["vn_sma200"] = vn_sma200

    # ==================================================
    # 6. ĐIỀU KIỆN RS
    # ==================================================

    latest["rs_pass"] = (
        latest["rs_percentile"] >= 70
    )

    # ==================================================
    # 7. ĐIỀU KIỆN MARKET REGIME
    # ==================================================

    latest["market_pass"] = (
        latest["market_regime"] == "Bullish"
    )

    # ==================================================
    # 8. KẾT QUẢ LAYER 2
    # ==================================================

    latest["layer2_pass"] = (
        latest["rs_pass"]
        &
        latest["market_pass"]
    )

    # ==================================================
    # 9. SẮP XẾP THEO RS
    # ==================================================

    latest = latest.sort_values(
        "rs_percentile",
        ascending=False
    )

    # ==================================================
    # 10. TRẢ KẾT QUẢ
    # ==================================================

    return latest[
        [
            "symbol",
            "time",
            "close",
            "return_6m",
            "rs_percentile",
            "vn_index",
            "vn_sma200",
            "market_regime",
            "rs_pass",
            "market_pass",
            "layer2_pass"
        ]
    ].reset_index(drop=True)


# ======================================================
# TEST ĐỘC LẬP
# ======================================================

if __name__ == "__main__":

    result = run_layer2()

    market_regime = result["market_regime"].iloc[0]
    vn_index = result["vn_index"].iloc[0]
    vn_sma200 = result["vn_sma200"].iloc[0]

    passed = result[
        result["layer2_pass"]
    ]

    print()
    print("===== LAYER 2 =====")
    print()

    print(f"VN-Index: {vn_index:.2f}")
    print(f"SMA200:   {vn_sma200:.2f}")

    print()

    print(
        "Market Regime:",
        market_regime
    )

    print(
        f"RS PASS: {len(passed)}/{len(result)}"
    )

    print()

    print(
        passed[
            ["symbol", "rs_percentile"]
        ].to_string(index=False)
    )