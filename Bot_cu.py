from __future__ import annotations

import os
import json
import io
import math
import asyncio
from datetime import date, timedelta

import matplotlib
matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

import DNSE

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
if not TOKEN:
    raise RuntimeError(
        "Thiếu TELEGRAM_BOT_TOKEN trong file .env"
    )

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SUBSCRIBERS_PATH = os.path.join(
    BASE_DIR,
    "subscribers.json",
)
SUBSCRIBERS: set[int] = set()


def load_subscribers() -> None:
    global SUBSCRIBERS
    try:
        with open(SUBSCRIBERS_PATH, encoding="utf-8") as file:
            SUBSCRIBERS = {int(user_id) for user_id in json.load(file)}
    except (FileNotFoundError, ValueError, TypeError):
        SUBSCRIBERS = set()


def save_subscribers() -> None:
    with open(SUBSCRIBERS_PATH, "w", encoding="utf-8") as file:
        json.dump(sorted(SUBSCRIBERS), file)


def load_daily_prices(symbol: str, days: int = 240) -> pd.DataFrame:
    end = date.today()
    start = end - timedelta(days=days)
    prices = DNSE.fetch_prices(symbol, start, end, "1D", source="auto")
    if prices.empty:
        raise ValueError(f"Khong co du lieu Daily cho {symbol}")
    prices = prices.copy()
    prices["time"] = pd.to_datetime(prices["time"])
    return prices.sort_values("time").drop_duplicates("time").reset_index(drop=True)


def calculate_analysis(prices: pd.DataFrame) -> dict:
    data = prices.copy()
    close = pd.to_numeric(data["close"], errors="coerce")
    high = pd.to_numeric(data["high"], errors="coerce")
    low = pd.to_numeric(data["low"], errors="coerce")

    data["sma20"] = close.rolling(20).mean()
    data["sma50"] = close.rolling(50).mean()
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    data["rsi14"] = 100 - (100 / (1 + gain / loss.replace(0, pd.NA)))
    true_range = pd.concat(
        [high - low, (high - close.shift()).abs(), (low - close.shift()).abs()],
        axis=1,
    ).max(axis=1)
    data["atr14"] = true_range.rolling(14).mean()
    data["support20"] = low.rolling(20).min()
    data["resistance20"] = high.rolling(20).max()
    data["volume_ma20"] = data["volume"].rolling(20).mean()

    latest = data.iloc[-1]
    close_value = float(latest["close"])
    atr = float(latest["atr14"])
    support = float(latest["support20"])
    resistance = float(latest["resistance20"])
    sma20 = float(latest["sma20"])
    sma50 = float(latest["sma50"])
    rsi = float(latest["rsi14"])
    volume = float(latest["volume"])
    volume_ma20 = float(latest["volume_ma20"])

    buy_low = support
    buy_high = min(close_value + atr * 0.5, resistance)
    stop = max(0, support - atr)
    target = resistance

    reasons = []
    if close_value > sma20 > sma50:
        reasons.append("xu hướng tăng: Giá > SMA20 > SMA50")
    elif close_value < sma20 < sma50:
        reasons.append("xu hướng giảm: Giá < SMA20 < SMA50")
    else:
        reasons.append("xu hướng chưa đồng thuận giữa Giá, SMA20 và SMA50")

    if 50 <= rsi <= 70:
        reasons.append(f"RSI14 {rsi:.1f} ở vùng khỏe")
    elif rsi > 70:
        reasons.append(f"RSI14 {rsi:.1f}, có thể quá mua")
    else:
        reasons.append(f"RSI14 {rsi:.1f}, động lượng yếu")

    if close_value > sma20 > sma50 and 45 <= rsi <= 70:
        recommendation = "MUA THĂM DÒ"
        action_icon = "🟢"
    elif close_value < sma20 < sma50 or rsi < 35:
        recommendation = "BÁN / GIẢM TỶ TRỌNG"
        action_icon = "🔴"
    else:
        recommendation = "THEO DÕI, CHƯA MUA"
        action_icon = "🟡"

    layer1 = {
        "status": "⚪ CHƯA ĐỦ DỮ LIỆU",
        "reason": "Cần gọi /detail để tải báo cáo tài chính.",
    }
    layer2_pass = close_value > sma20 > sma50
    layer3_pass = close_value > sma20 and volume >= volume_ma20
    layer4_pass = atr > 0 and stop < close_value < target

    return {
        "data": data,
        "latest": latest,
        "checked_at": date.today().isoformat(),
        "close": close_value,
        "sma20": sma20,
        "sma50": sma50,
        "rsi": rsi,
        "atr": atr,
        "support": support,
        "resistance": resistance,
        "buy_low": buy_low,
        "buy_high": buy_high,
        "stop": stop,
        "target": target,
        "recommendation": recommendation,
        "action_icon": action_icon,
        "reasons": reasons,
        "volume": volume,
        "volume_ma20": volume_ma20,
        "layer1": layer1,
        "layer2_pass": layer2_pass,
        "layer3_pass": layer3_pass,
        "layer4_pass": layer4_pass,
    }


def enrich_financial_layer(symbol: str, analysis: dict) -> dict:
    try:
        financials = DNSE.get_financials(symbol, "quarter", max_periods=4)
        latest = financials.iloc[0]
        roe = float(latest["roe"]) if pd.notna(latest["roe"]) else float("nan")
        net_margin = float(latest["net_margin"]) if pd.notna(latest["net_margin"]) else float("nan")
        passed = pd.notna(roe) and roe >= 0.10
        analysis["layer1"] = {
            "status": "✅ PASS" if passed else "🟡 THEO DÕI",
            "reason": f"ROE {roe:.1%} | Biên LN {net_margin:.1%} | Kỳ {latest['period']}",
        }
    except Exception as error:
        analysis["layer1"] = {"status": "⚪ CHƯA ĐỦ DỮ LIỆU", "reason": str(error)[:100]}
    return analysis


def money(value: float) -> str:
    return f"{value:,.0f}".replace(",", ".")


def format_analysis(symbol: str, analysis: dict) -> str:
    return (
        f"📊 <b>{symbol}</b>\n"
        f"📅 Ngày kiểm tra: <b>{analysis['checked_at']}</b>\n"
        f"💰 Giá đóng cửa: <b>{money(analysis['close'])}</b>\n\n"
        f"📈 SMA20: {money(analysis['sma20'])} | SMA50: {money(analysis['sma50'])}\n"
        f"⚡ RSI14: {analysis['rsi']:.1f} | ATR14: {money(analysis['atr'])}\n\n"
        f"🟩 Vùng mua tham khảo: <b>{money(analysis['buy_low'])} - {money(analysis['buy_high'])}</b>\n"
        f"🟥 Vùng bán/kháng cự: <b>{money(analysis['resistance'])}</b>\n"
        f"🛡️ Stop tham khảo: {money(analysis['stop'])}\n"
        f"🎯 Mục tiêu gần: {money(analysis['target'])}\n\n"
        f"{analysis['action_icon']} <b>KẾT LUẬN: {analysis['recommendation']}</b>\n"
        f"🧠 Lý do:\n• " + "\n• ".join(analysis["reasons"]) +
        "\n\n⚠️ Chỉ mang tính học tập, không phải khuyến nghị đầu tư."
    )


def symbol_keyboard(symbol: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔎 Chi tiết 4 tầng", callback_data=f"detail:{symbol}"),
         InlineKeyboardButton("📈 Chart", callback_data=f"chart:{symbol}")],
        [InlineKeyboardButton("🔔 Theo dõi", callback_data=f"subscribe:{symbol}"),
         InlineKeyboardButton("📊 Chỉ báo", callback_data=f"indicators:{symbol}")],
    ])


def format_detail(symbol: str, analysis: dict) -> str:
    return (
        f"🧩 <b>PHÂN TÍCH CHI TIẾT: {symbol}</b>\n"
        f"📅 Ngày kiểm tra: <b>{analysis['checked_at']}</b>\n\n"
        f"1️⃣ <b>QUALITY / CƠ BẢN</b>: {analysis['layer1']['status']}\n"
        f"   └ {analysis['layer1']['reason']}\n\n"
        f"2️⃣ <b>DAILY TREND</b>: {'✅ PASS' if analysis['layer2_pass'] else '❌ FAIL'}\n"
        f"   └ Giá {money(analysis['close'])} | SMA20 {money(analysis['sma20'])} | SMA50 {money(analysis['sma50'])}\n\n"
        f"3️⃣ <b>MOMENTUM</b>: {'✅ PASS' if analysis['layer3_pass'] else '❌ FAIL'}\n"
        f"   └ RSI14 {analysis['rsi']:.1f} | Volume {money(analysis['volume'])} | MA20 Vol {money(analysis['volume_ma20'])}\n\n"
        f"4️⃣ <b>RISK / ATR</b>: {'✅ PASS' if analysis['layer4_pass'] else '⚠️ THEO DÕI'}\n"
        f"   └ ATR14 {money(analysis['atr'])} | Stop {money(analysis['stop'])} | Target {money(analysis['target'])}\n\n"
        f"🏁 <b>KẾT LUẬN: {analysis['action_icon']} {analysis['recommendation']}</b>\n"
        "⚠️ Đây là bộ lọc tham khảo, không phải khuyến nghị đầu tư."
    )


def make_chart(symbol: str, analysis: dict) -> io.BytesIO:
    data = analysis["data"].tail(120)
    figure, axis = plt.subplots(figsize=(11, 6), dpi=150)
    axis.plot(data["time"], data["close"], color="#1769aa", linewidth=2, label="Close")
    axis.plot(data["time"], data["sma20"], color="#f39c12", linewidth=1.4, label="SMA20")
    axis.plot(data["time"], data["sma50"], color="#8e44ad", linewidth=1.4, label="SMA50")
    axis.axhline(analysis["support"], color="#2e9d59", linestyle="--", label="Support 20D")
    axis.axhline(analysis["resistance"], color="#d64545", linestyle="--", label="Resistance 20D")
    axis.axhline(analysis["stop"], color="#c0392b", linestyle=":", label="Stop")
    axis.fill_between(
        data["time"],
        analysis["buy_low"],
        analysis["buy_high"],
        color="#8fd19e",
        alpha=0.2,
        label="Buy zone",
    )
    axis.set_title(f"{symbol} | {analysis['checked_at']} | {analysis['recommendation']}")
    axis.set_ylabel("Price")
    axis.grid(alpha=0.2)
    axis.legend(loc="upper left", ncol=3, fontsize=8)
    axis.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))
    figure.autofmt_xdate()
    figure.tight_layout()
    output = io.BytesIO()
    figure.savefig(output, format="png", bbox_inches="tight")
    plt.close(figure)
    output.seek(0)
    return output


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 <b>Fintech Signal Bot</b>\n\n"
        "📌 /check FPT - tóm tắt một mã\n"
        "🧩 /detail FPT - xem đủ 4 tầng\n"
        "📉 /chart FPT - vẽ biểu đồ kỹ thuật\n"
        "📋 /signals - quét danh sách mẫu\n"
        "🌡️ /regime - xem trạng thái thị trường\n"
        "📂 /positions - vị thế đang theo dõi\n"
        "🔔 /subscribe - bật thông báo\n"
        "🔕 /unsubscribe - tắt thông báo\n"
        "💚 /status - tình trạng dữ liệu bot\n"
        "ℹ️ /about - giải thích chiến lược\n"
        "❓ /help - xem hướng dẫn",
        parse_mode="HTML",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)


async def get_symbol(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    if not context.args:
        return None
    symbol = context.args[0].upper().strip()
    if not symbol.isalnum() or not 3 <= len(symbol) <= 10:
        return None
    return symbol


async def check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    symbol = await get_symbol(context)
    if not symbol:
        await update.message.reply_text("📝 Cách dùng: /check FPT")
        return
    try:
        await update.message.reply_text(f"⏳ Đang phân tích {symbol}...")
        prices = await asyncio.to_thread(load_daily_prices, symbol)
        analysis = calculate_analysis(prices)
        await update.message.reply_text(format_analysis(symbol, analysis), parse_mode="HTML", reply_markup=symbol_keyboard(symbol))
    except Exception as error:
        await update.message.reply_text(f"❌ Không phân tích được {symbol}: {error}")


async def detail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    symbol = await get_symbol(context)
    if not symbol:
        await update.message.reply_text("📝 Cách dùng: /detail FPT")
        return
    try:
        await update.message.reply_text(f"⏳ Đang tải đủ 4 tầng cho {symbol}...")
        prices = await asyncio.to_thread(load_daily_prices, symbol)
        analysis = await asyncio.to_thread(enrich_financial_layer, symbol, calculate_analysis(prices))
        await update.message.reply_text(format_detail(symbol, analysis), parse_mode="HTML", reply_markup=symbol_keyboard(symbol))
    except Exception as error:
        await update.message.reply_text(f"❌ Không phân tích chi tiết được {symbol}: {error}")


async def chart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    symbol = await get_symbol(context)
    if not symbol:
        await update.message.reply_text("📝 Cách dùng: /chart FPT")
        return
    try:
        await update.message.reply_text(f"🎨 Đang vẽ chart {symbol}...")
        prices = await asyncio.to_thread(load_daily_prices, symbol)
        analysis = calculate_analysis(prices)
        image = await asyncio.to_thread(make_chart, symbol, analysis)
        await update.message.reply_photo(
            photo=InputFile(image, filename=f"{symbol.lower()}_chart.png"),
            caption=f"📈 {symbol} | Ngày kiểm tra: {analysis['checked_at']}\n{analysis['action_icon']} {analysis['recommendation']}",
        )
    except Exception as error:
        await update.message.reply_text(f"❌ Không vẽ được chart {symbol}: {error}")


async def signals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    results = []
    await update.message.reply_text("🔎 Đang quét danh sách cổ phiếu...")
    for symbol in DEFAULT_SYMBOLS:
        try:
            prices = await asyncio.to_thread(load_daily_prices, symbol, 120)
            analysis = calculate_analysis(prices)
            results.append(
                f"{analysis['action_icon']} <b>{symbol}</b>: {analysis['recommendation']} | Giá {money(analysis['close'])}"
            )
        except Exception:
            results.append(f"⚪ <b>{symbol}</b>: không đủ dữ liệu")
    await update.message.reply_text(
        f"📋 <b>TÍN HIỆU NGÀY {date.today().isoformat()}</b>\n\n" + "\n".join(results),
        parse_mode="HTML",
    )


async def regime(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        prices = await asyncio.to_thread(load_daily_prices, "VNINDEX", 300)
        analysis = calculate_analysis(prices)
        market = "🟢 TĂNG" if analysis["close"] > analysis["sma50"] else "🔴 YẾU"
        await update.message.reply_text(
            f"🌡️ <b>MARKET REGIME</b>\n"
            f"📅 Ngày kiểm tra: {analysis['checked_at']}\n"
            f"VN-Index: <b>{money(analysis['close'])}</b>\n"
            f"SMA50: {money(analysis['sma50'])}\n"
            f"Trạng thái: <b>{market}</b>",
            parse_mode="HTML",
        )
    except Exception as error:
        await update.message.reply_text(f"❌ Không lấy được regime: {error}")


async def positions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📂 <b>POSITIONS</b>\n\n"
        "Hiện bot chưa kết nối tài khoản giao dịch nên chưa có vị thế thực tế.\n"
        "Dùng /subscribe để nhận thông báo tín hiệu từ bộ lọc.",
        parse_mode="HTML",
    )


async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None:
        return
    SUBSCRIBERS.add(update.effective_user.id)
    save_subscribers()
    await update.message.reply_text("🔔 Đã bật theo dõi tín hiệu cho tài khoản này.")


async def unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None:
        return
    SUBSCRIBERS.discard(update.effective_user.id)
    save_subscribers()
    await update.message.reply_text("🔕 Đã tắt theo dõi tín hiệu.")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "💚 <b>STATUS</b>\n"
        f"📅 Ngày hệ thống: {date.today().isoformat()}\n"
        f"👥 Người đăng ký: {len(SUBSCRIBERS)}\n"
        "📡 Dữ liệu: DNSE → vnstock → CSV dự phòng\n"
        "🧮 Phân tích: Daily, RSI, SMA, Volume, ATR\n"
        "📈 Chart: đang sẵn sàng",
        parse_mode="HTML",
    )


async def about(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "ℹ️ <b>ABOUT FINTECH SIGNAL BOT</b>\n\n"
        "Bot dùng dữ liệu thị trường để minh họa quy trình lọc cổ phiếu nhiều tầng:\n"
        "1️⃣ Quality: chỉ số tài chính\n"
        "2️⃣ Daily Trend: SMA20/SMA50\n"
        "3️⃣ Momentum: RSI và Volume\n"
        "4️⃣ Risk: ATR, Stop và Target\n\n"
        "⚠️ Phục vụ học tập/nghiên cứu, không phải khuyến nghị đầu tư.",
        parse_mode="HTML",
    )


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    action, symbol = query.data.split(":", 1)
    prices = await asyncio.to_thread(load_daily_prices, symbol)
    analysis = calculate_analysis(prices)
    if action == "detail":
        analysis = await asyncio.to_thread(enrich_financial_layer, symbol, analysis)
        await query.message.reply_text(format_detail(symbol, analysis), parse_mode="HTML", reply_markup=symbol_keyboard(symbol))
    elif action == "chart":
        image = await asyncio.to_thread(make_chart, symbol, analysis)
        await query.message.reply_photo(InputFile(image, filename=f"{symbol.lower()}_chart.png"))
    elif action == "subscribe":
        if update.effective_user:
            SUBSCRIBERS.add(update.effective_user.id)
            save_subscribers()
        await query.message.reply_text("🔔 Đã bật theo dõi tín hiệu.")
    elif action == "indicators":
        volume_ratio = analysis["volume"] / analysis["volume_ma20"] if analysis["volume_ma20"] else 0
        await query.message.reply_text(
            f"📊 <b>{symbol} CHỈ BÁO</b>\n"
            f"SMA20: {money(analysis['sma20'])}\nSMA50: {money(analysis['sma50'])}\n"
            f"RSI14: {analysis['rsi']:.1f}\nATR14: {money(analysis['atr'])}\n"
            f"Volume/MA20: {volume_ratio:.2f}x",
            parse_mode="HTML",
        )


def main() -> None:
    load_subscribers()
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("check", check))
    application.add_handler(CommandHandler("detail", detail))
    application.add_handler(CommandHandler("chart", chart))
    application.add_handler(CommandHandler("signals", signals))
    application.add_handler(CommandHandler("regime", regime))
    application.add_handler(CommandHandler("positions", positions))
    application.add_handler(CommandHandler("subscribe", subscribe))
    application.add_handler(CommandHandler("unsubscribe", unsubscribe))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("about", about))
    application.add_handler(CallbackQueryHandler(button_callback, pattern=r"^(detail|chart|subscribe|indicators):"))
    print("🤖 Telegram bot đang chạy...")
    application.run_polling()


if __name__ == "__main__":
    main()
