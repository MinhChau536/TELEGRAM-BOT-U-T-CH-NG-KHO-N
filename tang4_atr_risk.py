"""
tang4_atr_risk.py — Tầng 4: Dynamic ATR Risk Management + Backtest
Chiến lược: Regime-Adaptive Quality Momentum & Dynamic ATR Sizing

Nguyên tắc: làm ĐÚNG như tài liệu chiến lược của nhóm; backtest PHÁT LẠI từng nến 15 phút thật
theo thứ tự thời gian, giống cách bot chạy thật (mỗi nến mới: kiểm tra Stop, chốt lời, thoát xu hướng).

THEO TÀI LIỆU
  - ATR14 = MA14(TR) trên dữ liệu DAILY, chỉ dùng đến phiên trước ngày tín hiệu.
  - Stop Loss = Entry − 2×ATR14; số cổ phiếu = Vốn×Risk% / (2×ATR14), làm tròn theo lô 100.
  - Giá vào lệnh = giá mở cửa nến 15 phút kế tiếp sau nến tín hiệu (tài liệu: open_15m dùng cho Entry/backtest).
  - Chốt 50% khi giá đạt Entry + 4×ATR14; 50% còn lại trailing stop 2×ATR14 từ đỉnh.
  - Thứ tự ưu tiên thoát: S1 (ATR Stop) → chốt lời/trailing → S2 (Trend Exit) → S3 (Fundamental Exit).

TÀI LIỆU KHÔNG NÓI RÕ (đã chọn cách đọc sát nhất; đều có tham số để đổi)
  - Khóa bán T+2: TẮT (sellable_from=0). Phí/thuế: 0 (fee, sell_tax). Bật khi muốn kịch bản thực tế.
  - S2 = EMA20 < EMA50 trong 2 nến 15 phút liên tiếp (công thức trong bản dán bị mất, cần đối chiếu tài liệu gốc).
  - Trong cùng một nến chạm cả Stop và mức chốt lời thì tính Stop trước; gap qua mức nào thì khớp giá mở cửa.
  - Chốt 50% làm tròn xuống lô 100; nếu vị thế < 2 lô thì chốt toàn bộ tại +4×ATR.

Dữ liệu: giá phải tính bằng ĐỒNG (không phải nghìn đồng), cùng đơn vị với vốn.
"""
from __future__ import annotations

import os
import warnings

import numpy as np
import pandas as pd

LOT = 100


# ---------------------------------------------------------------- 4.1 – 4.2 ATR
def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    return pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def atr14(df: pd.DataFrame, period: int = 14, method: str = "sma") -> pd.Series:
    tr = true_range(df)
    if method == "wilder":
        return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    return tr.rolling(period).mean()


# ---------------------------------------------------- 4.3 – 4.4 Stop & Position Sizing
def stop_and_size(entry, atr, capital, risk_pct=0.01, k=2.0, lot=LOT):
    """StopLoss = Entry − k×ATR; Shares = Capital×Risk% / RiskPerShare (làm tròn xuống lô)."""
    if not np.isfinite(atr) or atr <= 0:
        return None
    stop = entry - k * atr
    if stop <= 0:
        return None
    risk_per_share = entry - stop
    raw = capital * risk_pct / risk_per_share
    shares = int(raw // lot) * lot
    shares = min(shares, int((capital // entry) // lot) * lot)  # không vượt số vốn có
    return {
        "stop_loss": stop,
        "risk_per_share": risk_per_share,
        "raw_shares": raw,
        "shares": shares,
    }


# ------------------------------------------------------------ Mô phỏng 1 giao dịch
def simulate_trade(
    ticker,
    entry_time,
    entry_price,
    daily: pd.DataFrame,
    capital=100_000_000,
    risk_pct=0.01,
    k=2.0,
    tp_mult=4.0,
    sellable_from=0,
    atr_method="sma",
    exit_flags: pd.Series | None = None,  # index = date, giá trị = lý do (S2/S3), NaN = không thoát
    fee=0.0,
    sell_tax=0.0,
):
    if entry_price < 1000:
        warnings.warn(f"{ticker}: giá {entry_price} có vẻ tính theo nghìn đồng, hãy nhân 1000.")

    d = daily.sort_values("date").reset_index(drop=True).copy()
    d["date"] = pd.to_datetime(d["date"])
    d["atr"] = atr14(d, method=atr_method)

    entry_date = pd.Timestamp(entry_time).normalize()
    idx = d.index[d["date"].dt.normalize() == entry_date]
    if len(idx) == 0 or idx[0] == 0:
        return None
    i0 = idx[0]

    atr = d.loc[i0 - 1, "atr"]  # ATR đến phiên trước tín hiệu
    sz = stop_and_size(entry_price, atr, capital, risk_pct, k)
    if sz is None or sz["shares"] <= 0:
        return None

    shares = sz["shares"]
    stop = sz["stop_loss"]
    tp = entry_price + tp_mult * atr
    remaining, partial, peak = shares, False, entry_price
    fills, last_j = [], i0  # (date, qty, price, reason)

    for j in range(i0, len(d)):
        bar, age = d.loc[j], j - i0
        last_j = j
        if age >= sellable_from:
            # S1 — ATR Stop / Trailing Stop (ưu tiên cao nhất)
            eff_stop = max(stop, peak - k * atr) if partial else stop
            if bar["low"] <= eff_stop:
                px = min(bar["open"], eff_stop)
                fills.append((bar["date"], remaining, px, "Trailing Stop" if partial else "ATR Stop"))
                remaining = 0
                break
            # Chốt lời tại +4×ATR
            if (not partial) and bar["high"] >= tp:
                px = max(bar["open"], tp)
                qty = int((remaining * 0.5) // LOT) * LOT
                if qty == 0:
                    fills.append((bar["date"], remaining, px, "Take Profit (toàn bộ)"))
                    remaining = 0
                    break
                fills.append((bar["date"], qty, px, "Take Profit 50%"))
                remaining -= qty
                partial = True
            # S2 / S3 — thoát theo tín hiệu từ tầng khác, khớp giá đóng cửa
            if exit_flags is not None and bar["date"] in exit_flags.index and pd.notna(exit_flags[bar["date"]]):
                fills.append((bar["date"], remaining, bar["close"], str(exit_flags[bar["date"]])))
                remaining = 0
                break
        peak = max(peak, bar["high"])

    if remaining > 0:  # hết dữ liệu mà vẫn còn vị thế → tất toán theo giá đóng cửa cuối
        fills.append((d.loc[last_j, "date"], remaining, d.loc[last_j, "close"], "End of data"))

    cost = shares * entry_price * (1 + fee)
    proceeds = sum(q * p * (1 - fee - sell_tax) for _, q, p, _ in fills)
    pnl = proceeds - cost
    planned_risk = shares * sz["risk_per_share"]
    return {
        "ticker": ticker,
        "entry_date": d.loc[i0, "date"],
        "entry_price": entry_price,
        "atr14": atr,
        "stop_loss": stop,
        "take_profit": tp,
        "shares": shares,
        "exit_date": fills[-1][0],
        "exit_reason": fills[-1][3],
        "hold_days": last_j - i0,
        "pnl": pnl,
        "return_pct": pnl / cost,
        "r_multiple": pnl / planned_risk,
        "n_fills": len(fills),
    }


# ------------------------------------------------------------------- Backtest
def simulate_trade_15m(
    ticker,
    entry_time,
    entry_price,
    bars: pd.DataFrame,
    daily: pd.DataFrame,
    capital=100_000_000,
    risk_pct=0.01,
    k=2.0,
    tp_mult=4.0,
    sellable_from=0,
    atr_method="sma",
    use_s2=True,
    exit_flags: pd.Series | None = None,  # S3: index = ngày, giá trị = lý do; thoát ở nến cuối ngày đó
    fee=0.0,
    sell_tax=0.0,
):
    """Phát lại từng nến 15 phút của MỘT mã kể từ nến vào lệnh, như bot chạy thật.
    bars cần cột: time, open, high, low, close, ema20, ema50 (đầu ra Tầng 3)."""
    b = bars.sort_values("time").reset_index(drop=True)
    b["time"] = pd.to_datetime(b["time"])
    entry_time = pd.Timestamp(entry_time)
    hit = b.index[b["time"] == entry_time]
    if len(hit) == 0:
        return None
    i0 = hit[0]

    d = daily.sort_values("date").reset_index(drop=True).copy()
    d["atr"] = atr14(d, method=atr_method)
    prior = d[d["date"] < entry_time.normalize()]
    if prior.empty:
        return None
    atr = prior["atr"].iloc[-1]
    sz = stop_and_size(entry_price, atr, capital, risk_pct, k)
    if sz is None or sz["shares"] <= 0:
        return None

    shares, stop = sz["shares"], sz["stop_loss"]
    tp = entry_price + tp_mult * atr
    days = b["time"].dt.normalize()
    day_rank = {dt: n for n, dt in enumerate(sorted(days.unique()))}
    remaining, partial, peak, below = shares, False, entry_price, 0
    fills, last_j = [], i0

    for j in range(i0, len(b)):
        bar = b.iloc[j]
        last_j = j
        age = day_rank[days.iloc[j]] - day_rank[days.iloc[i0]]
        can_sell = age >= sellable_from
        if can_sell:
            # S1 — ATR Stop / Trailing Stop
            eff_stop = max(stop, peak - k * atr) if partial else stop
            if bar["low"] <= eff_stop:
                px = min(bar["open"], eff_stop)
                fills.append((bar["time"], remaining, px, "Trailing Stop" if partial else "ATR Stop"))
                remaining = 0
                break
            # Chốt lời 50% tại +4×ATR
            if (not partial) and bar["high"] >= tp:
                px = max(bar["open"], tp)
                qty = int((remaining * 0.5) // LOT) * LOT
                if qty == 0:
                    fills.append((bar["time"], remaining, px, "Take Profit (toàn bộ)"))
                    remaining = 0
                    break
                fills.append((bar["time"], qty, px, "Take Profit 50%"))
                remaining -= qty
                partial = True
        # S2 — Trend Exit: EMA20 < EMA50 trong 2 nến liên tiếp, bán ở giá đóng cửa nến thứ hai
        below = below + 1 if bar["ema20"] < bar["ema50"] else 0
        if can_sell and use_s2 and below >= 2:
            fills.append((bar["time"], remaining, bar["close"], "Trend Exit (S2)"))
            remaining = 0
            break
        # S3 — Fundamental Exit: thoát ở nến cuối của ngày có cờ
        if can_sell and exit_flags is not None:
            day = days.iloc[j]
            last_of_day = j == len(b) - 1 or days.iloc[j + 1] != day
            if last_of_day and day in exit_flags.index and pd.notna(exit_flags[day]):
                fills.append((bar["time"], remaining, bar["close"], str(exit_flags[day])))
                remaining = 0
                break
        peak = max(peak, bar["high"])

    if remaining > 0:
        fills.append((b.loc[last_j, "time"], remaining, b.loc[last_j, "close"], "End of data"))

    cost = shares * entry_price * (1 + fee)
    proceeds = sum(q * p * (1 - fee - sell_tax) for _, q, p, _ in fills)
    pnl = proceeds - cost
    planned_risk = shares * sz["risk_per_share"]
    return {
        "ticker": ticker,
        "entry_date": entry_time,
        "entry_price": entry_price,
        "atr14": atr,
        "stop_loss": stop,
        "take_profit": tp,
        "shares": shares,
        "exit_date": fills[-1][0],
        "exit_reason": fills[-1][3],
        "hold_days": day_rank[days.iloc[last_j]] - day_rank[days.iloc[i0]],
        "pnl": pnl,
        "return_pct": pnl / cost,
        "r_multiple": pnl / planned_risk,
        "n_fills": len(fills),
    }


def run_backtest(
    signals: pd.DataFrame,
    daily_by_ticker: dict,
    capital=100_000_000,
    risk_pct=0.01,
    exit_flags_by_ticker: dict | None = None,  # {ticker: Series(date -> lý do S3)}
    bars15: pd.DataFrame | None = None,  # có -> phát lại theo nến 15 phút (cột symbol, time, OHLC, ema20, ema50)
    **kw,
):
    """Chạy lần lượt các tín hiệu theo thời gian; mỗi mã chỉ giữ 1 vị thế tại một thời điểm.
    Khối lượng mỗi lệnh tính theo vốn ban đầu (chưa kiểm tra tổng tiền các vị thế mở đồng thời).
    Có bars15: dùng simulate_trade_15m (khuyến nghị). Không có: dùng simulate_trade trên nến daily."""
    bars_by = None
    if bars15 is not None:
        bars_by = {t: g.sort_values("time").reset_index(drop=True) for t, g in bars15.groupby("symbol")}
    trades, busy_until = [], {}
    for s in signals.sort_values("entry_time").itertuples():
        d = daily_by_ticker.get(s.ticker)
        if d is None:
            continue
        flags = (exit_flags_by_ticker or {}).get(s.ticker)
        if bars_by is not None:
            b = bars_by.get(s.ticker)
            if b is None or (s.ticker in busy_until and pd.Timestamp(s.entry_time) <= busy_until[s.ticker]):
                continue
            t = simulate_trade_15m(s.ticker, s.entry_time, s.entry_price, b, d, capital, risk_pct,
                                   exit_flags=flags, **kw)
        else:
            if s.ticker in busy_until and pd.Timestamp(s.entry_time).normalize() <= busy_until[s.ticker]:
                continue
            t = simulate_trade(s.ticker, s.entry_time, s.entry_price, d, capital, risk_pct,
                               exit_flags=flags, **kw)
        if t:
            trades.append(t)
            busy_until[s.ticker] = t["exit_date"]
    return pd.DataFrame(trades)


def performance(trades: pd.DataFrame, capital=100_000_000) -> dict:
    if trades.empty:
        return {"n_trades": 0}
    t = trades.sort_values("exit_date")
    eq = capital + t.set_index("exit_date")["pnl"].cumsum()
    eq = pd.concat([pd.Series([capital], index=[t["entry_date"].min()]), eq])
    years = max((eq.index.max() - eq.index.min()).days / 365.25, 1e-9)
    total = eq.iloc[-1] / capital - 1
    gains, losses = t.loc[t.pnl > 0, "pnl"].sum(), -t.loc[t.pnl <= 0, "pnl"].sum()
    return {
        "n_trades": len(t),
        "total_return": total,
        "CAGR (quy đổi năm, chỉ tham khảo nếu < 1 năm)": (1 + total) ** (1 / years) - 1,
        "max_drawdown (theo lệnh đã đóng)": (eq / eq.cummax() - 1).min(),
        "win_rate": (t.pnl > 0).mean(),
        "profit_factor": gains / losses if losses > 0 else np.inf,
        "avg_R": t["r_multiple"].mean(),
        "avg_hold_days": t["hold_days"].mean(),
    }


# --------------------------------------------------- Nạp dữ liệu từ pipeline của nhóm
def load_daily_from_db(db_path, symbols=None, resolution="1D", price_multiplier=1.0):
    """Đọc bảng `prices` (market_data.db do API.py tạo) -> {ticker: DataFrame daily}.
    Đổi tên symbol->ticker, time->date. Nếu nguồn trả giá theo NGHÌN ĐỒNG thì đặt price_multiplier=1000."""
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query(
            "SELECT symbol, time, open, high, low, close, volume FROM prices WHERE resolution = ?",
            conn,
            params=(resolution,),
        )
    finally:
        conn.close()
    df = df.rename(columns={"symbol": "ticker", "time": "date"})
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    if symbols is not None:
        df = df[df["ticker"].isin({str(s).upper() for s in symbols})]
    df[["open", "high", "low", "close"]] = df[["open", "high", "low", "close"]] * price_multiplier
    return {t: g.sort_values("date").reset_index(drop=True) for t, g in df.groupby("ticker")}


def daily_from_frame(df: pd.DataFrame, symbols=None, price_multiplier: float = 1.0) -> dict:
    """DataFrame giá 1D (đầu ra của DNSE.collect_prices: symbol, time, open, high, low, close, volume)
    -> {ticker: DataFrame daily có cột date}. Dùng khi dữ liệu lấy trực tiếp từ DNSE.py thay vì SQLite."""
    d = df.rename(columns={"symbol": "ticker", "time": "date"}).copy()
    d["ticker"] = d["ticker"].astype(str).str.upper().str.strip()
    d["date"] = pd.to_datetime(d["date"]).dt.normalize()
    if symbols is not None:
        d = d[d["ticker"].isin({str(s).upper() for s in symbols})]
    cols = ["open", "high", "low", "close"]
    d[cols] = d[cols].astype(float) * price_multiplier
    d = d.drop_duplicates(["ticker", "date"], keep="last")
    return {t: g.sort_values("date").reset_index(drop=True) for t, g in d.groupby("ticker")}


def load_universe(csv_path, exclude_sectors=None):
    """Danh sách mã từ file CSV của các tầng (tier1_pass_symbols.csv, layer2_pass_symbols.csv,
    layer3_pass_symbols.csv đều có cột `symbol`). Nếu file có cột `sector` thì có thể loại ngành,
    ví dụ exclude_sectors=["BANK", "SECURITIES", "INSURANCE"]."""
    df = pd.read_csv(csv_path)
    if exclude_sectors and "sector" in df.columns:
        df = df[~df["sector"].astype(str).str.upper().isin({s.upper() for s in exclude_sectors})]
    return df["symbol"].astype(str).str.upper().str.strip().drop_duplicates().tolist()


def build_orders(
    signals,
    daily_by_ticker: dict,
    capital=100_000_000,
    risk_pct=0.01,
    k=2.0,
    tp_mult=4.0,
    atr_method="sma",
    enforce_cash=True,
):
    """Chế độ chạy thật cho bot: nhận các mã đang BUY của Tầng 3 và trả bảng lệnh Tầng 4.
    signals = DataFrame hoặc đường dẫn CSV có cột symbol, time, close (như layer3_pass_symbols.csv).
    ATR14 daily lấy đến phiên TRƯỚC ngày tín hiệu; giá vào lệnh = close của nến tín hiệu.
    enforce_cash=True: tổng giá trị các lệnh không vượt vốn; xử lý theo thứ tự dòng trong signals,
    lệnh nào không đủ tiền thì giảm khối lượng (cash_limited=True) hoặc bỏ."""
    if isinstance(signals, (str, os.PathLike)):
        signals = pd.read_csv(signals)
    rows, used = [], 0.0
    for r in signals.itertuples():
        sym = str(r.symbol).upper()
        d = daily_by_ticker.get(sym)
        if d is None:
            continue
        d = d.sort_values("date").reset_index(drop=True).copy()
        d["atr"] = atr14(d, method=atr_method)
        prior = d[d["date"] < pd.Timestamp(r.time).normalize()]
        if prior.empty:
            continue
        atr = prior["atr"].iloc[-1]
        entry = float(r.close)
        sz = stop_and_size(entry, atr, capital, risk_pct, k)
        if sz is None or sz["shares"] <= 0:
            continue
        shares = sz["shares"]
        if enforce_cash:
            room = int(((capital - used) // entry) // LOT) * LOT
            shares = min(shares, room)
            if shares <= 0:
                continue
        used += shares * entry
        rows.append(
            {
                "symbol": sym,
                "signal_time": r.time,
                "entry_price": entry,
                "atr14_daily": atr,
                "stop_loss": sz["stop_loss"],
                "take_profit_4atr": entry + tp_mult * atr,
                "risk_per_share": sz["risk_per_share"],
                "shares": shares,
                "cash_limited": shares < sz["shares"],
                "risk_capital": capital * risk_pct,
                "risk_amount": shares * sz["risk_per_share"],
                "position_value": shares * entry,
            }
        )
    return pd.DataFrame(rows)


def signals_from_layer3(l3: pd.DataFrame, gate: pd.DataFrame | None = None) -> pd.DataFrame:
    """Chuyển kết quả calculate_layer3() (cần cột symbol, time, open, layer3_pass) thành
    bảng tín hiệu [ticker, entry_time, entry_price] cho run_backtest.

    - Nến đóng cửa mới biết tín hiệu -> vào lệnh ở giá MỞ CỬA của nến 15 phút kế tiếp của cùng mã.
    - gate (tuỳ chọn) = DataFrame [symbol, date, ok] gộp từ Tầng 1/2 theo ngày; hàm dùng giá trị của
      phiên TRƯỚC ngày tín hiệu (không dùng dữ liệu cuối ngày để lọc tín hiệu trong ngày)."""
    d = l3.sort_values(["symbol", "time"]).copy()
    d["time"] = pd.to_datetime(d["time"])
    d["next_time"] = d.groupby("symbol")["time"].shift(-1)
    d["next_open"] = d.groupby("symbol")["open"].shift(-1)
    s = d[d["layer3_pass"].astype(bool) & d["next_open"].notna()].copy()

    if gate is not None and not s.empty:
        g = gate[["symbol", "date", "ok"]].copy()
        g["date"] = pd.to_datetime(g["date"]).dt.normalize()
        g = g.sort_values("date")
        s["sig_date"] = s["time"].dt.normalize()
        s = pd.merge_asof(
            s.sort_values("sig_date"), g,
            left_on="sig_date", right_on="date", by="symbol", allow_exact_matches=False,
        )
        s = s[s["ok"].fillna(False).astype(bool)]

    out = s.rename(columns={"symbol": "ticker", "next_time": "entry_time", "next_open": "entry_price"})
    return out[["ticker", "entry_time", "entry_price"]].sort_values("entry_time").reset_index(drop=True)


# ------------------------------------------------------------------- Chạy thử
if __name__ == "__main__":
    # 1) Ví dụ trong tài liệu: kỳ vọng shares = 500, stop = 48.000
    print(stop_and_size(50_000, 1_000, 100_000_000, 0.01))

    # 2) Dữ liệu giả để kiểm tra luồng
    rng = np.random.default_rng(42)
    dates = pd.bdate_range("2026-01-01", periods=120)
    close = 50_000 + np.cumsum(rng.normal(150, 600, len(dates)))
    df = pd.DataFrame({"ticker": "AAA", "date": dates, "close": close})
    df["open"] = df["close"].shift(1).fillna(df["close"])
    df["high"] = df[["open", "close"]].max(axis=1) + rng.uniform(0, 500, len(df))
    df["low"] = df[["open", "close"]].min(axis=1) - rng.uniform(0, 500, len(df))
    df["volume"] = 1_000_000

    sig = pd.DataFrame(
        {
            "ticker": "AAA",
            "entry_time": [dates[30] + pd.Timedelta(hours=10), dates[60] + pd.Timedelta(hours=10)],
            "entry_price": [df.loc[30, "open"], df.loc[60, "open"]],
        }
    )
    tr = run_backtest(sig, {"AAA": df})
    print(tr.T)
    print(performance(tr))
