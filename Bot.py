from __future__ import annotations

import asyncio
import io
import json
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

import DNSE


load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
SUBSCRIBERS_FILE = BASE_DIR / "subscribers.json"

if not TOKEN:
    raise RuntimeError("Thiếu TELEGRAM_BOT_TOKEN trong file .env")


VN30_SYMBOLS = [
    "FPT", "VCB", "VIC", "VHM", "HPG",
    "MWG", "MSN", "SSI", "VNM", "GAS",
    "MBB", "TCB", "CTG", "ACB", "VPB",
    "PVT", "BID", "BCM", "PLX", "SAB",
    "SHB", "STB", "VJC", "VRE", "POW",
    "HDB", "VIB", "GVR", "DGC", "SSB",
]

INDUSTRIES = {
    "FPT": "Công nghệ",
    "VCB": "Ngân hàng",
    "VIC": "Bất động sản",
    "VHM": "Bất động sản",
    "HPG": "Thép",
    "MWG": "Bán lẻ",
    "MSN": "Tiêu dùng",
    "SSI": "Chứng khoán",
    "VNM": "Thực phẩm",
    "GAS": "Dầu khí",
    "MBB": "Ngân hàng",
    "TCB": "Ngân hàng",
    "CTG": "Ngân hàng",
    "ACB": "Ngân hàng",
    "VPB": "Ngân hàng",
    "PVT": "Vận tải - Logistics",
    "BID": "Ngân hàng",
    "BCM": "Bất động sản",
    "PLX": "Dầu khí",
    "SAB": "Thực phẩm",
    "SHB": "Ngân hàng",
    "STB": "Ngân hàng",
    "VJC": "Vận tải - Logistics",
    "VRE": "Bất động sản",
    "POW": "Điện",
    "HDB": "Ngân hàng",
    "VIB": "Ngân hàng",
    "GVR": "Cao su",
    "DGC": "Hóa chất",
    "SSB": "Ngân hàng",
}

# Danh sách mã mặc định dùng khi người dùng chưa /subscribe mã nào,
# để lệnh /portfolio và /sector luôn có dữ liệu để hiển thị.
DEFAULT_PORTFOLIO_CAPITAL = 10_000_000.0

CACHE_TTL = 300
CACHE: dict[str, tuple[float, object]] = {}

ANALYSIS_POOL = ThreadPoolExecutor(max_workers=8)
DATA_POOL = ThreadPoolExecutor(max_workers=12)


def cache_get(key: str):
    item = CACHE.get(key)

    if not item:
        return None

    created_at, value = item

    if time.time() - created_at > CACHE_TTL:
        CACHE.pop(key, None)
        return None

    return value


def cache_set(key: str, value) -> None:
    CACHE[key] = (time.time(), value)


def number(value, default: float = 0.0) -> float:
    try:
        value = float(value)

        if math.isfinite(value):
            return value

    except (TypeError, ValueError):
        pass

    return default


def fmt(value, digits: int = 2) -> str:
    return (
        f"{number(value):,.{digits}f}"
        .replace(",", "_")
        .replace(".", ",")
        .replace("_", ".")
    )


def percent(value) -> str:
    return f"{number(value) * 100:.1f}%"


def price_fmt(value) -> str:
    """Format giá/chỉ báo dạng giá với 2 chữ số thập phân.

    Trước đây bot dùng fmt(value, 0) cho giá, khiến mọi mức giá bị làm
    tròn về số nguyên (vd 66.85 -> 67). Hàm này giữ 2 chữ số thập phân
    để phản ánh đúng bước giá thực tế của cổ phiếu.
    """
    return fmt(value, 2)


def pass_text(value: bool) -> str:
    return "🟢 PASS" if value else "🔴 FAIL"


def load_json(path: Path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass

    return default


def save_json(path: Path, value) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def clean_columns(data: pd.DataFrame) -> pd.DataFrame:
    data = data.copy()
    data.columns = [
        str(column).strip().lower().replace(" ", "_")
        for column in data.columns
    ]
    return data


def find_column(
    data: pd.DataFrame,
    names: list[str],
) -> str | None:
    columns = {
        str(column).lower().replace("_", "").replace("-", ""):
        column
        for column in data.columns
    }

    for name in names:
        key = name.lower().replace("_", "").replace("-", "")

        if key in columns:
            return columns[key]

    return None


def read_csv(filename: str) -> pd.DataFrame:
    path = BASE_DIR / filename

    if not path.exists():
        return pd.DataFrame()

    try:
        return clean_columns(pd.read_csv(path))
    except Exception:
        return pd.DataFrame()


def load_prices(symbol: str, days: int = 420) -> pd.DataFrame:
    symbol = symbol.upper()
    cache_key = f"prices:{symbol}:{days}"
    cached = cache_get(cache_key)

    if isinstance(cached, pd.DataFrame):
        return cached.copy()

    end = date.today()
    start = end - timedelta(days=days)

    try:
        data = DNSE.fetch_prices(
            symbol,
            start,
            end,
            "1D",
            source="auto",
        )
    except Exception:
        data = pd.DataFrame()

    if data is None or data.empty:
        data = read_csv("prices_15.csv")

        if not data.empty:
            symbol_column = find_column(
                data,
                ["symbol", "ticker", "code", "stock_code"],
            )

            if symbol_column:
                data = data[
                    data[symbol_column].astype(str).str.upper()
                    == symbol
                ]

    if data is None or data.empty:
        raise ValueError(f"Không có dữ liệu giá cho {symbol}")

    data = clean_columns(data)

    data = data.rename(
        columns={
            "date": "time",
            "datetime": "time",
            "timestamp": "time",
            "price": "close",
            "vol": "volume",
        }
    )

    if "time" not in data.columns:
        raise ValueError("Dữ liệu giá thiếu cột time")

    if "close" not in data.columns:
        raise ValueError("Dữ liệu giá thiếu cột close")

    for column in ["open", "high", "low", "volume"]:
        if column not in data.columns:
            data[column] = 0

    data["time"] = pd.to_datetime(
        data["time"],
        errors="coerce",
    )

    for column in ["open", "high", "low", "close", "volume"]:
        data[column] = pd.to_numeric(
            data[column],
            errors="coerce",
        )

    data = data.dropna(
        subset=["time", "close"],
    )

    data = (
        data.sort_values("time")
        .drop_duplicates("time")
        .reset_index(drop=True)
    )

    cache_set(cache_key, data)
    return data.copy()


def calculate_rsi(close: pd.Series, period: int = 14):
    change = close.diff()
    gain = change.clip(lower=0)
    loss = -change.clip(upper=0)

    average_gain = gain.ewm(
        alpha=1 / period,
        min_periods=period,
        adjust=False,
    ).mean()

    average_loss = loss.ewm(
        alpha=1 / period,
        min_periods=period,
        adjust=False,
    ).mean()

    relative_strength = average_gain / average_loss.replace(
        0,
        float("nan"),
    )

    return 100 - (100 / (1 + relative_strength))


def calculate_indicators(
    prices: pd.DataFrame,
) -> pd.DataFrame:
    data = prices.copy()

    close = data["close"]
    high = data["high"]
    low = data["low"]
    volume = data["volume"].fillna(0)

    data["previous_close"] = close.shift(1)

    data["ema20"] = close.ewm(
        span=20,
        adjust=False,
    ).mean()

    data["ema50"] = close.ewm(
        span=50,
        adjust=False,
    ).mean()

    data["sma20"] = close.rolling(20).mean()
    data["sma50"] = close.rolling(50).mean()
    data["sma200"] = close.rolling(200).mean()

    data["return_6m"] = close / close.shift(126) - 1
    data["volume_ma20"] = volume.rolling(20).mean()

    data["rsi14"] = calculate_rsi(close)

    ema12 = close.ewm(
        span=12,
        adjust=False,
    ).mean()

    ema26 = close.ewm(
        span=26,
        adjust=False,
    ).mean()

    data["macd"] = ema12 - ema26

    data["macd_signal"] = data["macd"].ewm(
        span=9,
        adjust=False,
    ).mean()

    data["macd_histogram"] = (
        data["macd"] - data["macd_signal"]
    )

    data["bb_mid"] = close.rolling(20).mean()
    standard_deviation = close.rolling(20).std()

    data["bb_upper"] = (
        data["bb_mid"] + 2 * standard_deviation
    )

    data["bb_lower"] = (
        data["bb_mid"] - 2 * standard_deviation
    )

    lowest14 = low.rolling(14).min()
    highest14 = high.rolling(14).max()

    data["stoch_k"] = (
        (close - lowest14)
        / (highest14 - lowest14).replace(0, float("nan"))
        * 100
    )

    true_range = pd.concat(
        [
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)

    data["atr14"] = true_range.rolling(14).mean()
    data["obv"] = (
        (volume * close.diff().fillna(0).apply(
            lambda value: 1 if value > 0 else -1 if value < 0 else 0
        )).cumsum()
    )

    return data


def load_financials(symbol: str) -> pd.DataFrame:
    symbol = symbol.upper()
    cache_key = f"financials:{symbol}"
    cached = cache_get(cache_key)

    if isinstance(cached, pd.DataFrame):
        return cached.copy()

    try:
        data = DNSE.get_financials(
            symbol,
            "quarter",
            max_periods=12,
        )
    except Exception:
        data = pd.DataFrame()

    if data is None or data.empty:
        data = read_csv("financials.csv")

        if not data.empty:
            symbol_column = find_column(
                data,
                ["symbol", "ticker", "code", "stock_code"],
            )

            if symbol_column:
                data = data[
                    data[symbol_column].astype(str).str.upper()
                    == symbol
                ]

    if data is None or data.empty:
        raise ValueError(f"Không có dữ liệu tài chính cho {symbol}")

    data = clean_columns(data)

    date_column = find_column(
        data,
        [
            "report_date",
            "period",
            "quarter",
            "fiscal_date",
            "date",
        ],
    )

    if date_column:
        data["_period"] = pd.to_datetime(
            data[date_column],
            errors="coerce",
        )

        data = data.sort_values(
            "_period",
            ascending=False,
        )

    data = data.reset_index(drop=True)
    cache_set(cache_key, data)

    return data.copy()


def financial_quality(symbol: str) -> dict:
    data = load_financials(symbol)

    roe_column = find_column(
        data,
        ["roe", "roe_ttm", "return_on_equity"],
    )

    profit_column = find_column(
        data,
        [
            "profit_after_tax",
            "pat",
            "net_profit",
            "net_income",
        ],
    )

    if not roe_column:
        raise ValueError("Thiếu dữ liệu ROE")

    if not profit_column:
        raise ValueError(
            "Thiếu profit_after_tax trong financials.csv"
        )

    data[roe_column] = pd.to_numeric(
        data[roe_column],
        errors="coerce",
    )

    data[profit_column] = pd.to_numeric(
        data[profit_column],
        errors="coerce",
    )

    data = data.dropna(
        subset=[roe_column, profit_column],
    ).reset_index(drop=True)

    if len(data) < 5:
        raise ValueError("Cần tối thiểu 5 quý dữ liệu")

    roe_values = data[roe_column].head(4)

    if roe_values.abs().median() > 1:
        roe_values = roe_values / 100

    current_profit = number(data.iloc[0][profit_column])
    previous_year_profit = number(data.iloc[4][profit_column])

    if previous_year_profit == 0:
        raise ValueError("Không thể tính Profit YoY")

    profit_yoy = (
        current_profit - previous_year_profit
    ) / abs(previous_year_profit)

    roe_ttm = number(roe_values.mean())

    return {
        "profit_yoy": profit_yoy,
        "roe_ttm": roe_ttm,
        "profit_pass": profit_yoy >= 0.15,
        "roe_pass": roe_ttm >= 0.10,
        "pass": (
            profit_yoy >= 0.15
            and roe_ttm >= 0.10
        ),
    }


def get_return(symbol: str):
    try:
        data = calculate_indicators(
            load_prices(symbol, 420)
        )

        value = data.iloc[-1]["return_6m"]

        if pd.notna(value):
            return symbol, float(value)

    except Exception:
        pass

    return symbol, None


def calculate_rs() -> dict[str, float]:
    cached = cache_get("rs:vn30")

    if isinstance(cached, dict):
        return cached

    futures = [
        DATA_POOL.submit(get_return, symbol)
        for symbol in VN30_SYMBOLS
    ]

    values = {}

    for future in futures:
        symbol, value = future.result()

        if value is not None:
            values[symbol] = value

    cache_set("rs:vn30", values)
    return values


def market_regime() -> bool:
    cached = cache_get("market:regime")

    if cached is not None:
        return bool(cached)

    data = calculate_indicators(
        load_prices("VNINDEX", 420)
    )

    latest = data.iloc[-1]

    result = (
        number(latest["sma200"]) > 0
        and number(latest["close"])
        > number(latest["sma200"])
    )

    cache_set("market:regime", result)
    return result


def analyze(symbol: str) -> dict:
    symbol = symbol.upper()

    prices = calculate_indicators(
        load_prices(symbol, 420)
    )

    if len(prices) < 200:
        raise ValueError("Cần tối thiểu 200 phiên giá")

    latest = prices.iloc[-1]
    previous = prices.iloc[-2]

    close = number(latest["close"])
    previous_close = number(
        latest["previous_close"],
        previous["close"],
    )

    ema20 = number(latest["ema20"])
    ema50 = number(latest["ema50"])
    sma20 = number(latest["sma20"])
    sma50 = number(latest["sma50"])
    sma200 = number(latest["sma200"])
    volume = number(latest["volume"])
    volume_ma20 = number(latest["volume_ma20"])
    return_6m = number(latest["return_6m"])
    rsi14 = number(latest["rsi14"])
    macd = number(latest["macd"])
    macd_signal = number(latest["macd_signal"])
    macd_histogram = number(latest["macd_histogram"])
    bb_upper = number(latest["bb_upper"])
    bb_lower = number(latest["bb_lower"])
    stoch_k = number(latest["stoch_k"])
    atr14 = number(previous["atr14"])

    if atr14 <= 0:
        raise ValueError("Không tính được ATR14")

    rs_values = calculate_rs()

    rank = sum(
        value <= return_6m
        for value in rs_values.values()
    )

    rs_percentile = (
        rank / len(rs_values) * 100
        if rs_values
        else 0
    )

    quality = financial_quality(symbol)
    regime = market_regime()

    layer1 = quality["pass"]

    # Daily Trend
    layer2 = (
        close > sma20
        and sma20 > sma50
    )

    # Momentum
    layer3 = (
        ema20 > ema50
        and volume >= volume_ma20
        and close > previous_close
    )

    return {
        "symbol": symbol,
        "industry": INDUSTRIES.get(
            symbol,
            "Chưa xác định",
        ),
        "date": date.today().isoformat(),
        "close": close,
        "previous_close": previous_close,
        "ema20": ema20,
        "ema50": ema50,
        "sma20": sma20,
        "sma50": sma50,
        "sma200": sma200,
        "rsi14": rsi14,
        "macd": macd,
        "macd_signal": macd_signal,
        "macd_histogram": macd_histogram,
        "bb_upper": bb_upper,
        "bb_lower": bb_lower,
        "stoch_k": stoch_k,
        "volume": volume,
        "volume_ma20": volume_ma20,
        "return_6m": return_6m,
        "rs_percentile": rs_percentile,
        "regime": regime,
        "atr14": atr14,
        "stop_loss": close - 2 * atr14,
        "target": close + 4 * atr14,
        "buy_low": max(close - atr14, 0),
        "buy_high": close,
        "quality": quality,
        "layer1": layer1,
        "layer2": layer2,
        "layer3": layer3,
        "layer4": True,
    }


def smart_score(result: dict) -> float:
    """SmartScore 0-100: điểm tổng hợp dùng để phân bổ tỷ trọng danh mục.

    Không thay thế logic BUY/SELL của 4 tầng — chỉ là một thước đo liên
    tục (thay vì PASS/FAIL) để so sánh tương đối giữa các mã khi phân bổ
    vốn ở /portfolio. Trọng số:
      - Quality (30đ): Profit YoY pass (15) + ROE pass (15)
      - Daily Trend (25đ): Giá>SMA20 (8) + SMA20>SMA50 (8) + Regime (9)
      - Momentum (25đ): EMA20>EMA50 (8) + Volume>=MA20 (8) + Giá tăng (9)
      - Relative Strength (20đ): RS Percentile / 100 * 20
    """
    quality = result["quality"]
    score = 0.0

    score += 15.0 if quality["profit_pass"] else 0.0
    score += 15.0 if quality["roe_pass"] else 0.0

    score += 8.0 if result["close"] > result["sma20"] else 0.0
    score += 8.0 if result["sma20"] > result["sma50"] else 0.0
    score += 9.0 if result["regime"] else 0.0

    score += 8.0 if result["ema20"] > result["ema50"] else 0.0
    score += 8.0 if result["volume"] >= result["volume_ma20"] else 0.0
    score += 9.0 if result["close"] > result["previous_close"] else 0.0

    score += max(0.0, min(100.0, result["rs_percentile"])) / 100 * 20

    return round(score, 1)


def rsi_description(rsi: float) -> str:
    if rsi >= 70:
        return "quá mua, dễ rung lắc hoặc điều chỉnh"

    if rsi >= 55:
        return "động lượng mạnh"

    if rsi >= 45:
        return "trung tính, chưa quá mạnh cũng chưa quá yếu"

    if rsi >= 30:
        return "động lượng yếu"

    return "quá bán, có thể xuất hiện hồi kỹ thuật"


def decision(result: dict) -> tuple[str, str]:
    reasons = []

    quality = result["quality"]

    if not quality["profit_pass"]:
        reasons.append("Profit YoY chưa đạt 15%")

    if not quality["roe_pass"]:
        reasons.append("ROE chưa đạt 10%")

    if not result["layer2"]:
        reasons.append(
            "Daily Trend chưa đạt: cần Giá > SMA20 > SMA50"
        )

    if not result["layer3"]:
        reasons.append(
            "Momentum chưa đạt: EMA20, Volume hoặc Giá tăng"
        )

    if not reasons:
        reasons.append("Các điều kiện chính đều đạt")

    if all(
        result[key]
        for key in ["layer1", "layer2", "layer3", "layer4"]
    ):
        return "🟢 MUA THĂM DÒ", "; ".join(reasons)

    if result["layer1"] and result["layer2"]:
        return "🟡 THEO DÕI", "; ".join(reasons)

    return "🔴 KHÔNG MUA", "; ".join(reasons)


def quick_text(result: dict) -> str:
    conclusion, reason = decision(result)
    rsi = result["rsi14"]

    macd_status = (
        "tích cực"
        if result["macd"] > result["macd_signal"]
        else "tiêu cực"
    )

    stoch_status = (
        "quá mua"
        if result["stoch_k"] >= 80
        else "quá bán"
        if result["stoch_k"] <= 20
        else "trung tính"
    )

    return (
        f"📊 {result['symbol']}\n"
        f"🏭 Ngành: {result['industry']}\n"
        f"📅 Ngày kiểm tra: {result['date']}\n"
        f"💰 Giá đóng cửa: {price_fmt(result['close'])}\n\n"

        "📈 CHỈ BÁO CHÍNH\n"
        f"• EMA20: {price_fmt(result['ema20'])}\n"
        f"• EMA50: {price_fmt(result['ema50'])}\n"
        f"• SMA20: {price_fmt(result['sma20'])}\n"
        f"• SMA50: {price_fmt(result['sma50'])}\n"
        f"• RSI14: {fmt(rsi, 1)} "
        f"(tham khảo – {rsi_description(rsi)})\n"
        f"• MACD: {fmt(result['macd'], 2)} "
        f"(đang {macd_status})\n"
        f"• Stochastic %K: {fmt(result['stoch_k'], 1)} "
        f"({stoch_status})\n"
        f"• Volume: {fmt(result['volume'], 0)}\n"
        f"• Volume MA20: {fmt(result['volume_ma20'], 0)}\n"
        f"• ATR14: {price_fmt(result['atr14'])}\n\n"

        f"🟩 Vùng mua tham khảo: "
        f"{price_fmt(result['buy_low'])} – "
        f"{price_fmt(result['buy_high'])}\n"
        f"🟥 Vùng bán/kháng cự: "
        f"{price_fmt(result['bb_upper'])}\n"
        f"🛡 Stop Loss: "
        f"{price_fmt(result['stop_loss'])}\n"
        f"🎯 Mục tiêu gần: "
        f"{price_fmt(result['target'])}\n\n"

        "🧩 KẾT QUẢ 4 TẦNG\n"
        f"1️⃣ QUALITY / CƠ BẢN: "
        f"{pass_text(result['layer1'])}\n"
        f"2️⃣ DAILY TREND: "
        f"{pass_text(result['layer2'])}\n"
        f"3️⃣ MOMENTUM: "
        f"{pass_text(result['layer3'])}\n"
        f"4️⃣ RISK / ATR: "
        f"{pass_text(result['layer4'])}\n\n"

        f"🏁 KẾT LUẬN: {conclusion}\n"
        f"🧠 Lí do: {reason}\n\n"
        "⚠️ Không phải khuyến nghị đầu tư."
    )


def detail_text(result: dict) -> str:
    quality = result["quality"]
    conclusion, reason = decision(result)

    return (
        f"🧩 CHI TIẾT 4 TẦNG: {result['symbol']}\n"
        f"🏭 Ngành: {result['industry']}\n"
        f"📅 Ngày kiểm tra: {result['date']}\n"
        f"💰 Giá đóng cửa: {price_fmt(result['close'])}\n\n"

        f"1️⃣ QUALITY / CƠ BẢN: "
        f"{pass_text(result['layer1'])}\n"
        f"├ Profit YoY: {percent(quality['profit_yoy'])} "
        f"{pass_text(quality['profit_pass'])}\n"
        f"├ ROE TTM: {percent(quality['roe_ttm'])} "
        f"{pass_text(quality['roe_pass'])}\n"
        "└ Điều kiện: YoY ≥ 15%, ROE ≥ 10%\n\n"

        f"2️⃣ DAILY TREND: "
        f"{pass_text(result['layer2'])}\n"
        f"├ Giá > SMA20: "
        f"{pass_text(result['close'] > result['sma20'])}\n"
        f"├ SMA20 > SMA50: "
        f"{pass_text(result['sma20'] > result['sma50'])}\n"
        f"├ Return 6M: {percent(result['return_6m'])}\n"
        f"├ RS Percentile: "
        f"{result['rs_percentile']:.1f}\n"
        f"└ VN-Index > SMA200: "
        f"{pass_text(result['regime'])}\n\n"

        f"3️⃣ MOMENTUM: "
        f"{pass_text(result['layer3'])}\n"
        f"├ EMA20 > EMA50: "
        f"{pass_text(result['ema20'] > result['ema50'])}\n"
        f"├ Volume ≥ MA20: "
        f"{pass_text(result['volume'] >= result['volume_ma20'])}\n"
        f"└ Giá tăng: "
        f"{pass_text(result['close'] > result['previous_close'])}\n\n"

        f"4️⃣ RISK / ATR: "
        f"{pass_text(result['layer4'])}\n"
        f"├ ATR14 phiên trước: {price_fmt(result['atr14'])}\n"
        f"├ Stop Loss: {price_fmt(result['stop_loss'])}\n"
        f"└ Target: {price_fmt(result['target'])}\n\n"

        f"🏁 KẾT LUẬN: {conclusion}\n"
        f"🧠 Lí do: {reason}\n"
        "ℹ️ MACD/RSI14 chi tiết xem ở nút \"📊 Chỉ báo\".\n\n"
        "⚠️ Không phải khuyến nghị đầu tư."
    )


def get_watchlist(user_id: str) -> list[str]:
    data = load_json(SUBSCRIBERS_FILE, {})
    if not isinstance(data, dict):
        data = {}

    symbols = data.get(user_id, [])
    return symbols if isinstance(symbols, list) else []


async def analyze_many(symbols: list[str]) -> list[dict]:
    async def scan(symbol: str):
        try:
            return await run_analysis(symbol)
        except Exception:
            return None

    results = await asyncio.gather(
        *(scan(symbol) for symbol in symbols)
    )

    return [result for result in results if result]


def portfolio_text(results: list[dict], capital: float) -> str:
    if not results:
        return (
            "📊 TỐI ƯU DANH MỤC\n\n"
            "Chưa có mã nào để phân bổ.\n"
            "Dùng /subscribe <mã> để thêm vào watchlist, "
            "hoặc /portfolio <vốn> <mã1> <mã2> ..."
        )

    scored = [
        (result, smart_score(result))
        for result in results
    ]

    total_score = sum(score for _, score in scored) or 1.0

    lines = [
        "📊 TỐI ƯU DANH MỤC\n",
        f"💰 Tổng vốn: {fmt(capital, 0)} VNĐ\n",
        "📌 PHÂN BỔ ĐỀ XUẤT\n",
    ]

    for result, score in scored:
        weight = score / total_score
        conclusion, _ = decision(result)

        lines.append(
            f"📈 {result['symbol']}\n"
            f"• Tỷ trọng: {weight * 100:.1f}%\n"
            f"• Số tiền: {fmt(capital * weight, 0)} VNĐ\n"
            f"• SmartScore: {fmt(score, 1)}/100\n"
            f"• Tín hiệu: {conclusion}\n"
        )

    lines.append(
        "📌 Phương pháp:\n"
        "• SmartScore làm điểm cơ sở (Quality, Trend, "
        "Momentum, Relative Strength)\n"
        "• Tỷ trọng = SmartScore mã / Tổng SmartScore cả "
        "danh mục\n"
        "• Đây là gợi ý tham khảo, không tính đến mức độ rủi ro "
        "(ATR) hay giới hạn tỷ trọng tối đa mỗi mã.\n\n"
        "⚠️ Không phải khuyến nghị đầu tư."
    )

    return "\n".join(lines)


def sector_text(results: list[dict]) -> str:
    if not results:
        return "🏭 THEO NGÀNH\n\nKhông có mã đủ dữ liệu."

    groups: dict[str, list[dict]] = {}

    for result in results:
        groups.setdefault(result["industry"], []).append(result)

    bullish = market_regime()

    lines = [
        "🏭 TÍN HIỆU THEO NGÀNH\n",
        f"🌡️ Market Regime: "
        f"{'🟢 Bullish' if bullish else '🔴 Non-Bullish'} "
        f"(VN-Index so với SMA200)\n",
    ]

    for industry, items in sorted(
        groups.items(),
        key=lambda pair: -sum(
            smart_score(item) for item in pair[1]
        ) / len(pair[1]),
    ):
        avg_score = sum(
            smart_score(item) for item in items
        ) / len(items)

        buy_count = sum(
            1 for item in items if decision(item)[0].startswith("🟢")
        )

        lines.append(
            f"\n📂 {industry} "
            f"(SmartScore TB: {fmt(avg_score, 1)}, "
            f"{buy_count}/{len(items)} mã MUA THĂM DÒ)"
        )

        for item in sorted(
            items,
            key=lambda entry: -smart_score(entry),
        ):
            conclusion, _ = decision(item)
            lines.append(
                f"  {conclusion} {item['symbol']} "
                f"| SmartScore {fmt(smart_score(item), 1)}"
            )

    lines.append(
        "\nℹ️ SmartScore chỉ để so sánh tương đối giữa các mã, "
        "không phải điểm xác suất thắng.\n"
        "⚠️ Không phải khuyến nghị đầu tư."
    )

    return "\n".join(lines)


def buttons(symbol: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🔎 Chi tiết 4 tầng",
                    callback_data=f"detail:{symbol}",
                ),
                InlineKeyboardButton(
                    "📈 Chart",
                    callback_data=f"chart:{symbol}",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🔔 Theo dõi",
                    callback_data=f"subscribe:{symbol}",
                ),
                InlineKeyboardButton(
                    "📊 Chỉ báo",
                    callback_data=f"indicators:{symbol}",
                ),
            ],
            [
                InlineKeyboardButton(
                    "💼 Danh mục",
                    callback_data=f"portfolio:{symbol}",
                ),
                InlineKeyboardButton(
                    "🏭 Ngành",
                    callback_data=f"sector:{symbol}",
                ),
            ],
        ]
    )


def get_symbol(
    context: ContextTypes.DEFAULT_TYPE,
) -> str | None:
    if not context.args:
        return None

    symbol = context.args[0].strip().upper()

    if not symbol.isalnum():
        return None

    if not 3 <= len(symbol) <= 10:
        return None

    return symbol


async def run_analysis(symbol: str) -> dict:
    loop = asyncio.get_running_loop()

    return await loop.run_in_executor(
        ANALYSIS_POOL,
        analyze,
        symbol,
    )


def make_chart(symbol: str) -> io.BytesIO:
    import matplotlib

    matplotlib.use("Agg")

    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    data = calculate_indicators(
        load_prices(symbol, 240)
    ).tail(120)

    if data.empty:
        raise ValueError("Không có dữ liệu để vẽ biểu đồ")

    figure, (price_axis, volume_axis) = plt.subplots(
        2,
        1,
        figsize=(12, 7),
        dpi=140,
        sharex=True,
        gridspec_kw={"height_ratios": [3, 1]},
    )

    price_axis.plot(
        data["time"],
        data["close"],
        label="Close",
        linewidth=2,
        color="#1976D2",
    )

    price_axis.plot(
        data["time"],
        data["ema20"],
        label="EMA20",
        linewidth=1.2,
        color="#2E7D32",
    )

    price_axis.plot(
        data["time"],
        data["ema50"],
        label="EMA50",
        linewidth=1.2,
        color="#EF6C00",
    )

    price_axis.plot(
        data["time"],
        data["bb_upper"],
        label="BB Upper",
        linewidth=0.8,
        linestyle="--",
        color="#9E9E9E",
    )

    price_axis.plot(
        data["time"],
        data["bb_lower"],
        label="BB Lower",
        linewidth=0.8,
        linestyle="--",
        color="#9E9E9E",
    )

    volume_axis.bar(
        data["time"],
        data["volume"],
        width=1.5,
        color="#90CAF9",
        label="Volume",
    )

    volume_axis.plot(
        data["time"],
        data["volume_ma20"],
        color="#E53935",
        linewidth=1.2,
        label="Volume MA20",
    )

    price_axis.set_title(
        f"{symbol} - Fintech Signal Bot"
    )
    price_axis.set_ylabel("Giá")
    volume_axis.set_ylabel("Volume")
    price_axis.grid(alpha=0.25)
    volume_axis.grid(alpha=0.25)

    price_axis.legend(
        loc="upper left",
        fontsize=8,
    )

    volume_axis.legend(
        loc="upper left",
        fontsize=8,
    )

    price_axis.xaxis.set_major_formatter(
        mdates.DateFormatter("%d/%m")
    )

    figure.autofmt_xdate()
    figure.tight_layout()

    image = io.BytesIO()
    figure.savefig(
        image,
        format="png",
        bbox_inches="tight",
    )

    plt.close(figure)
    image.seek(0)

    return image


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await update.message.reply_text(
        "👋 Fintech Signal Bot\n\n"
        "📌 /check <mã> - tóm tắt một mã\n"
        "🧩 /detail <mã> - xem đủ 4 tầng\n"
        "📈 /chart <mã> - vẽ biểu đồ kỹ thuật\n"
        "📊 /indicators <mã> - toàn bộ chỉ báo kỹ thuật\n"
        "📋 /signals - quét danh sách mã tiêu biểu\n"
        "🔔 /subscribe <mã> - thêm vào watchlist\n"
        "🔕 /unsubscribe <mã> - xoá khỏi watchlist\n"
        "👀 /watchlist - trạng thái tín hiệu các mã đang theo dõi\n"
        "💼 /portfolio [vốn] [mã...] - phân bổ vốn theo SmartScore\n"
        "🏭 /sector - tín hiệu tổng hợp theo ngành\n"
        "🌡️ /regime - trạng thái VN-Index (Bullish/Non-Bullish)\n"
        "💚 /status - tình trạng dữ liệu bot\n"
        "ℹ️ /about - giải thích chiến lược & tính năng\n"
        "❓ /help - xem hướng dẫn\n\n"
        "Ví dụ: /check FPT"
    )


async def check(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    symbol = get_symbol(context)

    if not symbol:
        await update.message.reply_text(
            "Cách dùng: /check FPT"
        )
        return

    message = await update.message.reply_text(
        f"⌛ Đang phân tích {symbol}..."
    )

    try:
        result = await run_analysis(symbol)

        await message.edit_text(
            quick_text(result),
            reply_markup=buttons(symbol),
        )

    except Exception as error:
        await message.edit_text(
            f"❌ Không phân tích được {symbol}:\n{error}"
        )


async def detail(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    symbol = get_symbol(context)

    if not symbol:
        await update.message.reply_text(
            "Cách dùng: /detail FPT"
        )
        return

    message = await update.message.reply_text(
        f"⌛ Đang lấy chi tiết {symbol}..."
    )

    try:
        result = await run_analysis(symbol)

        await message.edit_text(
            detail_text(result),
            reply_markup=buttons(symbol),
        )

    except Exception as error:
        await message.edit_text(
            f"❌ Không lấy được dữ liệu:\n{error}"
        )


async def chart(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    symbol = get_symbol(context)

    if not symbol:
        await update.message.reply_text(
            "Cách dùng: /chart FPT"
        )
        return

    message = await update.message.reply_text(
        f"⌛ Đang tạo chart {symbol}..."
    )

    try:
        image = await asyncio.to_thread(
            make_chart,
            symbol,
        )

        await message.delete()

        await update.message.reply_photo(
            photo=image,
            caption=(
                f"📈 Biểu đồ kỹ thuật {symbol}\n"
                "Close, EMA20, EMA50, Bollinger Bands, Volume"
            ),
            reply_markup=buttons(symbol),
        )

    except Exception as error:
        await message.edit_text(
            f"❌ Không tạo được chart:\n{error}\n\n"
            "Hãy kiểm tra matplotlib đã được cài đặt."
        )


async def indicators_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    symbol = get_symbol(context)

    if not symbol:
        await update.message.reply_text(
            "Cách dùng: /indicators FPT"
        )
        return

    try:
        result = await run_analysis(symbol)

        await update.message.reply_text(
            f"📊 CHỈ BÁO: {symbol}\n\n"
            f"• Giá: {price_fmt(result['close'])}\n"
            f"• EMA20: {price_fmt(result['ema20'])}\n"
            f"• EMA50: {price_fmt(result['ema50'])}\n"
            f"• SMA20: {price_fmt(result['sma20'])}\n"
            f"• SMA50: {price_fmt(result['sma50'])}\n"
            f"• SMA200: {price_fmt(result['sma200'])}\n"
            f"• RSI14: {fmt(result['rsi14'], 1)} "
            f"(tham khảo - "
            f"{rsi_description(result['rsi14'])})\n"
            f"• MACD: {fmt(result['macd'], 2)}\n"
            f"• MACD Signal: "
            f"{fmt(result['macd_signal'], 2)}\n"
            f"• Stochastic %K: "
            f"{fmt(result['stoch_k'], 1)}\n"
            f"• Bollinger trên: "
            f"{price_fmt(result['bb_upper'])}\n"
            f"• Bollinger dưới: "
            f"{price_fmt(result['bb_lower'])}\n"
            f"• ATR14: {price_fmt(result['atr14'])}\n"
            f"• Volume: {fmt(result['volume'], 0)}\n"
            f"• Volume MA20: "
            f"{fmt(result['volume_ma20'], 0)}",
            reply_markup=buttons(symbol),
        )

    except Exception as error:
        await update.message.reply_text(
            f"❌ Không lấy được chỉ báo:\n{error}"
        )


async def signals(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await update.message.reply_text(
        f"⌛ Đang quét {len(VN30_SYMBOLS)} mã tiêu biểu..."
    )

    results = await analyze_many(VN30_SYMBOLS)
    bullish = market_regime()

    lines = [
        "📋 TÍN HIỆU CÁC MÃ TIÊU BIỂU\n",
        f"🌡️ Market Regime: "
        f"{'🟢 Bullish' if bullish else '🔴 Non-Bullish'}\n",
    ]

    for result in results:
        conclusion, _ = decision(result)

        lines.append(
            f"{conclusion} {result['symbol']} "
            f"| RSI {fmt(result['rsi14'], 1)} "
            f"| 6M {percent(result['return_6m'])}"
        )

    if not results:
        lines.append("Không có mã đủ dữ liệu.")

    lines.append(
        "\nℹ️ Chú giải:\n"
        "• RSI: sức mạnh giá 0-100, tham khảo (>70 quá mua, "
        "<30 quá bán), không phải điều kiện MUA/BÁN.\n"
        "• 6M: % thay đổi giá trong 6 tháng gần nhất (Return 6 "
        "tháng), dùng để xếp hạng sức mạnh tương đối (RS)."
    )

    await update.message.reply_text(
        "\n".join(lines)
    )


async def subscribe(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    symbol = get_symbol(context)

    if not symbol:
        await update.message.reply_text(
            "Cách dùng: /subscribe FPT"
        )
        return

    data = load_json(SUBSCRIBERS_FILE, {})
    if not isinstance(data, dict):
        data = {}

    if update.effective_user is None:
        await update.message.reply_text(
            "❌ Không xác định được tài khoản Telegram."
        )
        return

    user_id = str(update.effective_user.id)

    data.setdefault(user_id, [])

    if symbol in data[user_id]:
        await update.message.reply_text(
            f"🔔 {symbol} đã có trong danh sách theo dõi."
        )
        return

    data[user_id].append(symbol)
    save_json(SUBSCRIBERS_FILE, data)

    await update.message.reply_text(
        f"🔔 Đã bật theo dõi {symbol}.\n"
        "Mã đã được lưu vào subscribers.json."
    )


async def unsubscribe(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    data = load_json(SUBSCRIBERS_FILE, {})
    if not isinstance(data, dict):
        data = {}

    if update.effective_user is None:
        await update.message.reply_text(
            "❌ Không xác định được tài khoản Telegram."
        )
        return

    user_id = str(update.effective_user.id)
    symbol = get_symbol(context)

    if symbol:
        data[user_id] = [
            item
            for item in data.get(user_id, [])
            if item != symbol
        ]

        await update.message.reply_text(
            f"🔕 Đã tắt theo dõi {symbol}."
        )
    else:
        data.pop(user_id, None)

        await update.message.reply_text(
            "🔕 Đã tắt toàn bộ mã đang theo dõi."
        )

    save_json(SUBSCRIBERS_FILE, data)


async def status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    data = load_json(SUBSCRIBERS_FILE, {})
    if not isinstance(data, dict):
        data = {}

    if update.effective_user is None:
        await update.message.reply_text(
            "❌ Không xác định được tài khoản Telegram."
        )
        return

    user_id = str(update.effective_user.id)
    symbols = data.get(user_id, [])

    await update.message.reply_text(
        "💚 TRẠNG THÁI BOT\n\n"
        "🟢 Bot đang hoạt động\n"
        f"📦 Cache: {len(CACHE)} mục\n"
        f"🔔 Mã theo dõi: "
        f"{', '.join(symbols) if symbols else 'chưa có'}"
    )


async def regime(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = await update.message.reply_text(
        "⌛ Đang kiểm tra Market Regime..."
    )

    try:
        data = calculate_indicators(
            await asyncio.to_thread(
                load_prices,
                "VNINDEX",
                420,
            )
        )
        latest = data.iloc[-1]
        close = number(latest["close"])
        sma200 = number(latest["sma200"])
        bullish = sma200 > 0 and close > sma200

        await message.edit_text(
            "🌡️ MARKET REGIME\n\n"
            f"• VN-Index: {fmt(close, 2)}\n"
            f"• SMA200: {fmt(sma200, 2)}\n"
            f"• Trạng thái: "
            f"{'🟢 Bullish' if bullish else '🔴 Non-Bullish'}\n\n"
            "Điều kiện Tầng 2: VN-Index > SMA200."
        )
    except Exception as error:
        await message.edit_text(
            f"❌ Không lấy được Market Regime:\n{error}"
        )


async def watchlist(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """📋 Watchlist: các mã đã /subscribe, kèm trạng thái tín hiệu
    MUA/THEO DÕI/KHÔNG MUA hiện tại. Đây KHÔNG phải phân bổ vốn — xem
    /portfolio để phân bổ vốn theo SmartScore.
    """
    if update.effective_user is None:
        await update.message.reply_text(
            "❌ Không xác định được tài khoản Telegram."
        )
        return

    symbols = get_watchlist(str(update.effective_user.id))

    if not symbols:
        await update.message.reply_text(
            "🔔 WATCHLIST\n\n"
            "Chưa có mã nào đang theo dõi.\n"
            "Dùng /subscribe FPT để thêm mã."
        )
        return

    lines = ["🔔 WATCHLIST (trạng thái tín hiệu)\n"]

    for symbol in symbols:
        try:
            result = await run_analysis(symbol)
            conclusion, _ = decision(result)

            lines.append(
                f"{conclusion} {symbol} "
                f"| Giá {price_fmt(result['close'])} "
                f"| RSI {fmt(result['rsi14'], 1)} "
                f"| 6M {percent(result['return_6m'])}"
            )
        except Exception as error:
            lines.append(f"⚪ {symbol}: {str(error)[:80]}")

    lines.append(
        "\n💼 Muốn xem phân bổ vốn theo các mã này? Dùng /portfolio"
    )

    await update.message.reply_text("\n".join(lines))


async def positions(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Alias tương thích ngược: /positions == /watchlist."""
    await watchlist(update, context)


async def portfolio(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """💼 Portfolio: phân bổ vốn theo SmartScore cho một danh sách mã.

    Cách dùng:
      /portfolio                  -> dùng watchlist đã /subscribe,
                                      vốn mặc định 10.000.000 VNĐ
      /portfolio 5000000          -> dùng watchlist, vốn tùy chỉnh
      /portfolio 5000000 VIC FPT VNM -> chỉ định vốn và danh sách mã
    """
    if update.effective_user is None:
        await update.message.reply_text(
            "❌ Không xác định được tài khoản Telegram."
        )
        return

    args = list(context.args or [])
    capital = DEFAULT_PORTFOLIO_CAPITAL
    symbols: list[str] = []

    if args and args[0].replace(".", "").replace(",", "").isdigit():
        capital = number(
            args[0].replace(".", "").replace(",", ""),
            DEFAULT_PORTFOLIO_CAPITAL,
        )
        args = args[1:]

    for token in args:
        token = token.strip().upper()

        if token.isalnum() and 3 <= len(token) <= 10:
            symbols.append(token)

    if not symbols:
        symbols = get_watchlist(str(update.effective_user.id))

    if not symbols:
        await update.message.reply_text(
            "Chưa có mã nào để phân bổ.\n"
            "Dùng /subscribe <mã> để thêm vào watchlist, hoặc:\n"
            "/portfolio <vốn> <mã1> <mã2> ...\n"
            "Ví dụ: /portfolio 5000000 VIC FPT VNM"
        )
        return

    message = await update.message.reply_text(
        f"⌛ Đang tối ưu danh mục {len(symbols)} mã..."
    )

    results = await analyze_many(symbols)

    await message.edit_text(
        portfolio_text(results, capital)
    )


async def sector(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """🏭 Sector: nhóm tín hiệu theo ngành cho danh sách mã tiêu biểu."""
    message = await update.message.reply_text(
        "⌛ Đang tổng hợp tín hiệu theo ngành..."
    )

    results = await analyze_many(VN30_SYMBOLS)

    await message.edit_text(
        sector_text(results)
    )


async def about(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await update.message.reply_text(
        "ℹ️ CHIẾN LƯỢC 4 TẦNG\n\n"
        "1️⃣ Quality / Cơ bản:\n"
        "• Profit Growth YoY ≥ 15%\n"
        "• ROE TTM ≥ 10%\n\n"
        "2️⃣ Daily Trend:\n"
        "• Giá > SMA20 > SMA50\n"
        "• Theo dõi thêm Return 6M và RS Percentile\n\n"
        "3️⃣ Momentum:\n"
        "• EMA20 > EMA50\n"
        "• Volume ≥ Volume MA20\n"
        "• Giá đóng cửa tăng\n"
        "• MACD/RSI14 chi tiết chỉ hiện ở /indicators (tham khảo)\n\n"
        "4️⃣ Risk / ATR:\n"
        "• ATR14 của phiên trước\n"
        "• Stop Loss = Giá - 2×ATR\n"
        "• Target = Giá + 4×ATR\n\n"

        "🧰 TOÀN BỘ TÍNH NĂNG\n"
        "📌 /check <mã> - tóm tắt một mã\n"
        "🧩 /detail <mã> - xem đủ 4 tầng (không kèm MACD/RSI ở "
        "tầng 3, xem ở /indicators)\n"
        "📈 /chart <mã> - biểu đồ kỹ thuật\n"
        "📊 /indicators <mã> - toàn bộ chỉ báo (EMA/SMA/RSI/"
        "MACD/Stochastic/Bollinger/ATR)\n"
        "📋 /signals - quét tín hiệu các mã tiêu biểu + chú giải "
        "RSI, Return 6M\n"
        "🔔 /subscribe, /unsubscribe <mã> - quản lý watchlist\n"
        "👀 /watchlist (= /positions) - trạng thái tín hiệu các mã "
        "đang theo dõi\n"
        "💼 /portfolio [vốn] [mã...] - phân bổ vốn theo SmartScore "
        "(Quality + Trend + Momentum + RS)\n"
        "🏭 /sector - tín hiệu tổng hợp theo ngành, kèm Market Regime\n"
        "🌡️ /regime - trạng thái VN-Index so với SMA200\n"
        "💚 /status - tình trạng dữ liệu bot\n\n"

        "ℹ️ Watchlist vs Portfolio:\n"
        "• Watchlist = danh sách mã đang theo dõi trạng thái "
        "MUA/THEO DÕI/KHÔNG MUA.\n"
        "• Portfolio = gợi ý phân bổ VỐN cụ thể (%, số tiền) giữa "
        "các mã, dựa trên SmartScore.\n\n"
        "⚠️ Không phải khuyến nghị đầu tư."
    )


async def callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query

    try:
        await query.answer()
    except Exception:
        pass

    action, symbol = query.data.split(":", 1)

    if action == "subscribe":
        data = load_json(SUBSCRIBERS_FILE, {})
        user_id = str(query.from_user.id)

        data.setdefault(user_id, [])

        if symbol in data[user_id]:
            await query.answer(
                f"{symbol} đã được theo dõi.",
                show_alert=True,
            )
            return

        data[user_id].append(symbol)
        save_json(SUBSCRIBERS_FILE, data)

        await query.answer(
            f"Đã bật theo dõi {symbol}.",
            show_alert=True,
        )

        await query.message.reply_text(
            f"🔔 Đã bật theo dõi {symbol}."
        )
        return

    if action == "detail":
        try:
            result = await run_analysis(symbol)

            await query.message.reply_text(
                detail_text(result),
                reply_markup=buttons(symbol),
            )
        except Exception as error:
            await query.message.reply_text(
                f"❌ Lỗi: {error}"
            )

        return

    if action == "indicators":
        try:
            result = await run_analysis(symbol)

            await query.message.reply_text(
                f"📊 CHỈ BÁO: {symbol}\n\n"
                f"• EMA20: {price_fmt(result['ema20'])}\n"
                f"• EMA50: {price_fmt(result['ema50'])}\n"
                f"• SMA20: {price_fmt(result['sma20'])}\n"
                f"• SMA50: {price_fmt(result['sma50'])}\n"
                f"• RSI14: {fmt(result['rsi14'], 1)} "
                f"(tham khảo - "
                f"{rsi_description(result['rsi14'])})\n"
                f"• MACD: {fmt(result['macd'], 2)}\n"
                f"• MACD Signal: "
                f"{fmt(result['macd_signal'], 2)}\n"
                f"• Stochastic %K: "
                f"{fmt(result['stoch_k'], 1)}\n"
                f"• ATR14: {price_fmt(result['atr14'])}\n"
                f"• Volume: {fmt(result['volume'], 0)}\n"
                f"• Volume MA20: "
                f"{fmt(result['volume_ma20'], 0)}"
            )
        except Exception as error:
            await query.message.reply_text(
                f"❌ Lỗi: {error}"
            )

        return

    if action == "chart":
        try:
            image = await asyncio.to_thread(
                make_chart,
                symbol,
            )

            await query.message.reply_photo(
                photo=image,
                caption=f"📈 Biểu đồ kỹ thuật {symbol}",
                reply_markup=buttons(symbol),
            )
        except Exception as error:
            await query.message.reply_text(
                f"❌ Lỗi tạo chart:\n{error}\n\n"
                "Hãy kiểm tra matplotlib đã được cài đặt."
            )

        return

    if action == "portfolio":
        try:
            user_id = str(query.from_user.id)
            symbols = get_watchlist(user_id) or [symbol]

            results = await analyze_many(symbols)

            await query.message.reply_text(
                portfolio_text(
                    results,
                    DEFAULT_PORTFOLIO_CAPITAL,
                )
            )
        except Exception as error:
            await query.message.reply_text(
                f"❌ Lỗi tạo danh mục:\n{error}"
            )

        return

    if action == "sector":
        try:
            industry = INDUSTRIES.get(symbol, "Chưa xác định")
            peers = [
                item
                for item, name in INDUSTRIES.items()
                if name == industry
            ] or [symbol]

            results = await analyze_many(peers)

            await query.message.reply_text(
                sector_text(results)
            )
        except Exception as error:
            await query.message.reply_text(
                f"❌ Lỗi tổng hợp theo ngành:\n{error}"
            )


def main() -> None:
    application = (
        Application.builder()
        .token(TOKEN)
        .build()
    )

    handlers = {
        "start": start,
        "help": start,
        "check": check,
        "detail": detail,
        "chart": chart,
        "indicators": indicators_command,
        "signals": signals,
        "regime": regime,
        "positions": positions,
        "watchlist": watchlist,
        "portfolio": portfolio,
        "sector": sector,
        "subscribe": subscribe,
        "unsubscribe": unsubscribe,
        "status": status,
        "about": about,
    }

    for command, handler in handlers.items():
        application.add_handler(
            CommandHandler(command, handler)
        )

    application.add_handler(
        CallbackQueryHandler(callback)
    )

    print("🤖 Fintech Signal Bot đang chạy...")
    application.run_polling()


if __name__ == "__main__":
    main()
