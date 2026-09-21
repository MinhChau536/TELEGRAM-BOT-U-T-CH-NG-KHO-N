"""
tang4_atr_risk.py — Tầng 4: Dynamic ATR Risk Management + Backtest
Chiến lược: Regime-Adaptive Quality Momentum & Dynamic ATR Sizing

Schema dữ liệu daily (chữ thường): ticker, date, open, high, low, close, volume
Schema tín hiệu BUY (từ Tầng 3):   ticker, entry_time, entry_price

GIẢ ĐỊNH (chỉnh ở phần tham số nếu nhóm chọn khác):
  1. ATR14 tính trên dữ liệu DAILY, chỉ dùng đến phiên TRƯỚC ngày tín hiệu (tránh look-ahead).
  2. ATR14 = MA14(TR) đơn giản, đúng công thức tài liệu. Có tuỳ chọn method="wilder".
  3. T+2: không được bán trong 2 phiên đầu (sellable_from=2). Đặt 0 để tắt ràng buộc.
  4. Trong cùng một phiên, nếu chạm cả Stop và Take-profit thì tính Stop trước (thận trọng).
  5. Gap giảm qua Stop: khớp giá mở cửa. Gap tăng qua mức chốt lời: khớp giá mở cửa.
  6. Chốt 50% làm tròn xuống theo lô 100. Nếu < 2 lô thì chốt toàn bộ tại mức 4×ATR.
  7. Trailing Stop = đỉnh (tính đến hết phiên trước) − 2×ATR, chỉ bật sau khi đã chốt 50%.
  8. "S4" trong thứ tự ưu tiên SELL được hiểu là Take-profit / Trailing.
  9. Giá phải tính bằng ĐỒNG (không phải nghìn đồng), cùng đơn vị với vốn.
"""
from __future__ import annotations

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
    sellable_from=2,
    atr_method="sma",
    exit_flags: pd.Series | None = None,  # index = date, giá trị = lý do (S2/S3), NaN = không thoát
    fee=0.0015,
    sell_tax=0.001,
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
def run_backtest(
    signals: pd.DataFrame,
    daily_by_ticker: dict,
    capital=100_000_000,
    risk_pct=0.01,
    exit_flags_by_ticker: dict | None = None,  # {ticker: Series(date -> lý do S2/S3)}
    **kw,
):
    """Chạy lần lượt các tín hiệu theo thời gian; mỗi mã chỉ giữ 1 vị thế tại một thời điểm.
    Vốn dùng để tính khối lượng cố định = vốn ban đầu (đơn giản hoá).
    Lưu ý: chưa kiểm tra tổng tiền của các vị thế mở đồng thời."""
    trades, busy_until = [], {}
    for s in signals.sort_values("entry_time").itertuples():
        d = daily_by_ticker.get(s.ticker)
        if d is None:
            continue
        if s.ticker in busy_until and pd.Timestamp(s.entry_time).normalize() <= busy_until[s.ticker]:
            continue
        flags = (exit_flags_by_ticker or {}).get(s.ticker)
        t = simulate_trade(
            s.ticker, s.entry_time, s.entry_price, d, capital, risk_pct, exit_flags=flags, **kw
        )
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


def load_universe(tier1_pass_csv):
    """Danh sách mã đạt Tầng 1 (cột `symbol` trong tier1_pass.csv)."""
    return pd.read_csv(tier1_pass_csv)["symbol"].astype(str).str.upper().tolist()


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