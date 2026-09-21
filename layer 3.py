import sqlite3
import time
from datetime import date, timedelta

import pandas as pd

import Dnse_data_collector_manual as collector


# ============================================================
# CONFIG
# ============================================================

BASE_DIR = r"C:\Users\HP\Tang2_3_Strategy"
DB_PATH = BASE_DIR + r"\output\market_data.db"

RESOLUTION = "15"

EMA_FAST = 20
EMA_SLOW = 50
VOLUME_MA = 20
VOLUME_MULTIPLIER = 1.5


# ============================================================
# DATABASE
# ============================================================

def get_connection():
    return sqlite3.connect(DB_PATH)


def get_symbols_from_db():
    conn = get_connection()

    query = """
        SELECT DISTINCT symbol
        FROM prices
        WHERE resolution = '1D'
        ORDER BY symbol
    """

    df = pd.read_sql_query(query, conn)
    conn.close()

    return df["symbol"].tolist()


def get_15m_data(symbol):
    conn = get_connection()

    query = """
        SELECT
            symbol,
            resolution,
            time,
            open,
            high,
            low,
            close,
            volume,
            source
        FROM prices
        WHERE symbol = ?
          AND resolution = '15'
        ORDER BY time
    """

    df = pd.read_sql_query(
        query,
        conn,
        params=(symbol,)
    )

    conn.close()

    if df.empty:
        return df

    df["time"] = pd.to_datetime(df["time"])

    return df


# ============================================================
# LOAD 15M DATA FROM DNSE
# ============================================================

def save_15m_to_db(df):
    """
    Lưu dữ liệu 15m vào DB.
    INSERT OR IGNORE để không bị lỗi duplicate.
    """

    if df is None or df.empty:
        return 0

    columns = [
        "symbol",
        "resolution",
        "time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "source",
    ]

    df = df[columns].copy()

    conn = get_connection()
    cursor = conn.cursor()

    sql = """
        INSERT OR IGNORE INTO prices
        (
            symbol,
            resolution,
            time,
            open,
            high,
            low,
            close,
            volume,
            source
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    rows = [
        (
            row["symbol"],
            row["resolution"],
            str(row["time"]),
            row["open"],
            row["high"],
            row["low"],
            row["close"],
            row["volume"],
            row["source"],
        )
        for _, row in df.iterrows()
    ]

    cursor.executemany(sql, rows)

    inserted = cursor.rowcount

    conn.commit()
    conn.close()

    return inserted


def download_15m_symbol(
    symbol,
    start_date="2026-09-01",
    end_date="2026-09-18",
):
    """
    DNSE chỉ trả dữ liệu 15m của ngày được request.
    Vì vậy lấy từng ngày rồi ghép vào DB.
    """

    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)

    current = start
    total_rows = 0

    print()
    print(f"===== DOWNLOAD 15M: {symbol} =====")

    while current <= end:

        # Bỏ thứ 7, chủ nhật
        if current.weekday() < 5:

            try:
                print(
                    f"Lấy {symbol} - "
                    f"{current.strftime('%Y-%m-%d')}..."
                )

                df, source, warnings = collector.get_prices(
                    symbol=symbol,
                    start=current.date(),
                    end=current.date(),
                    resolution="15",
                    source="dnse",
                    closed_only=True,
                )

                if df is not None and not df.empty:

                    inserted = save_15m_to_db(df)

                    total_rows += inserted

                    print(
                        f"  API: {len(df)} nến | "
                        f"DB thêm: {inserted}"
                    )

                else:
                    print("  Không có dữ liệu.")

            except Exception as e:

                print(
                    f"  Lỗi {current.strftime('%Y-%m-%d')}: "
                    f"{type(e).__name__}: {e}"
                )

                # Không dừng toàn bộ chương trình
                # nếu một ngày lỗi.
                pass

            # Tránh gọi API quá nhanh
            time.sleep(0.5)

        current += timedelta(days=1)

    print()
    print(f"Tổng nến mới thêm cho {symbol}: {total_rows}")

    return total_rows


# ============================================================
# ENSURE ENOUGH 15M DATA
# ============================================================

def ensure_15m_data(
    symbol,
    min_bars=100,
    start_date="2026-09-01",
    end_date="2026-09-18",
):
    """
    Kiểm tra DB.
    Nếu chưa đủ dữ liệu 15m thì tải thêm từ DNSE.
    """

    df = get_15m_data(symbol)

    if len(df) >= min_bars:
        return df

    print()
    print(
        f"{symbol}: chỉ có {len(df)} nến 15m."
    )

    print(
        f"Cần ít nhất khoảng {min_bars} nến."
    )

    download_15m_symbol(
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
    )

    df = get_15m_data(symbol)

    return df


# ============================================================
# LAYER 3 CALCULATION
# ============================================================

def calculate_layer3(df):
    """
    Layer 3 - Intraday Momentum Trigger

    Điều kiện:

    1. EMA20 > EMA50
    2. Volume >= 1.5 * Volume MA20
       AND Close > Previous Close

    Momentum Score:
        EMA condition       = 1 điểm
        Volume/price        = 1 điểm

    BUY khi Score = 2
    """

    if df.empty:
        raise ValueError(
            "Không có dữ liệu 15m để tính Layer 3."
        )

    df = df.copy()

    df = df.sort_values(
        ["symbol", "time"]
    ).reset_index(drop=True)

    # --------------------------------------------------------
    # EMA20
    # --------------------------------------------------------

    df["ema20"] = (
        df.groupby("symbol")["close"]
        .transform(
            lambda s: s.ewm(
                span=EMA_FAST,
                adjust=False
            ).mean()
        )
    )

    # --------------------------------------------------------
    # EMA50
    # --------------------------------------------------------

    df["ema50"] = (
        df.groupby("symbol")["close"]
        .transform(
            lambda s: s.ewm(
                span=EMA_SLOW,
                adjust=False
            ).mean()
        )
    )

    # --------------------------------------------------------
    # Volume MA20
    # --------------------------------------------------------

    df["volume_ma20"] = (
        df.groupby("symbol")["volume"]
        .transform(
            lambda s: s.rolling(
                VOLUME_MA
            ).mean()
        )
    )

    # --------------------------------------------------------
    # Previous close
    # --------------------------------------------------------

    df["previous_close"] = (
        df.groupby("symbol")["close"]
        .shift(1)
    )

    # --------------------------------------------------------
    # Condition 1
    # EMA20 > EMA50
    # --------------------------------------------------------

    df["ema_condition"] = (
        df["ema20"] > df["ema50"]
    )

    # --------------------------------------------------------
    # Condition 2
    #
    # Volume >= 1.5 * MA20
    # AND Close > Previous Close
    # --------------------------------------------------------

    df["volume_condition"] = (
        (df["volume"] >=
         VOLUME_MULTIPLIER * df["volume_ma20"])
        &
        (df["close"] > df["previous_close"])
    )

    # --------------------------------------------------------
    # Momentum score
    # --------------------------------------------------------

    df["momentum_score"] = (
        df["ema_condition"].astype(int)
        +
        df["volume_condition"].astype(int)
    )

    # --------------------------------------------------------
    # Layer 3 PASS
    # --------------------------------------------------------

    df["layer3_pass"] = (
        df["momentum_score"] == 2
    )

    return df


# ============================================================
# GET LATEST SIGNAL
# ============================================================

def run_layer3_symbol(
    symbol,
    auto_download=True,
):
    """
    Chạy Layer 3 cho một mã.
    """

    if auto_download:

        df = ensure_15m_data(
            symbol=symbol,
            min_bars=100,
        )

    else:

        df = get_15m_data(symbol)

    if df.empty:
        raise ValueError(
            f"{symbol}: không có dữ liệu 15m."
        )

    result = calculate_layer3(df)

    latest = (
        result
        .sort_values("time")
        .iloc[-1]
        .copy()
    )

    return latest, result


# ============================================================
# FORMAT RESULT
# ============================================================

def format_layer3_result(latest):
    symbol = latest["symbol"]

    score = int(
        latest["momentum_score"]
    )

    if score == 2:
        signal = "PASS"
    else:
        signal = "FAIL"

    return {
        "symbol": symbol,
        "time": latest["time"],
        "close": latest["close"],
        "ema20": latest["ema20"],
        "ema50": latest["ema50"],
        "volume": latest["volume"],
        "volume_ma20": latest["volume_ma20"],
        "momentum_score": score,
        "layer3_pass": bool(
            latest["layer3_pass"]
        ),
        "signal": signal,
    }


# ============================================================
# RUN ALL SYMBOLS
# ============================================================

def run_layer3_all(
    symbols=None,
    auto_download=True,
):
    """
    Chạy Layer 3 cho toàn bộ universe.
    """

    if symbols is None:
        symbols = get_symbols_from_db()

    results = []

    print()
    print("==============================")
    print("      LAYER 3 INTRADAY")
    print("==============================")
    print()

    for i, symbol in enumerate(symbols, start=1):

        print(
            f"[{i}/{len(symbols)}] {symbol}"
        )

        try:

            latest, _ = run_layer3_symbol(
                symbol=symbol,
                auto_download=auto_download,
            )

            result = format_layer3_result(
                latest
            )

            results.append(result)

            print(
                f"  Close: {result['close']:.2f}"
            )

            print(
                f"  EMA20: {result['ema20']:.2f}"
            )

            print(
                f"  EMA50: {result['ema50']:.2f}"
            )

            print(
                f"  Momentum Score: "
                f"{result['momentum_score']}/2"
            )

            print(
                f"  Layer 3: "
                f"{result['signal']}"
            )

        except Exception as e:

            print(
                f"  ERROR: "
                f"{type(e).__name__}: {e}"
            )

        print()

    return pd.DataFrame(results)


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    # --------------------------------------------------------
    # TEST 1 SYMBOL TRƯỚC
    # --------------------------------------------------------

    result, detail = run_layer3_symbol(
        symbol="FPT",
        auto_download=True,
    )

    print()
    print("==============================")
    print("        LAYER 3 RESULT")
    print("==============================")
    print()

    print(
        f"Symbol:          {result['symbol']}"
    )

    print(
        f"Time:            {result['time']}"
    )

    print(
        f"Close:           {result['close']:.2f}"
    )

    print(
        f"EMA20:           {result['ema20']:.2f}"
    )

    print(
        f"EMA50:           {result['ema50']:.2f}"
    )

    print(
        f"Volume:          {result['volume']:,.0f}"
    )

    if pd.notna(result["volume_ma20"]):

        print(
            f"Volume MA20:     "
            f"{result['volume_ma20']:,.0f}"
        )

    else:

        print(
            "Volume MA20:     N/A"
        )

    print(
        f"Momentum Score:  "
        f"{int(result['momentum_score'])}/2"
    )

    print(
        f"Layer 3 PASS:    "
        f"{'YES' if result['layer3_pass'] else 'NO'}"
    )

    print()