"""
run_backtest_all.py — chạy thử MỘT LƯỢT Tầng 3 -> Tầng 4 -> backtest, dùng đúng code của các tầng.

Thư mục dự án (--dir) cần có:  DNSE.py, layer3.py (file Tầng 3), và output/tier1_pass_symbols.csv (Tầng 1).
Dữ liệu lấy qua DNSE.collect_prices giống cách Tầng 2 và Tầng 3 đang làm (không dùng SQLite nữa).

Ví dụ (Windows, một dòng):
    python run_backtest_all.py --dir C:\\Users\\HP\\Tang2_3_Strategy --layer3-file layer3.py

Kết quả ghi vào <dir>/output/tier4/: trades.csv (backtest), orders_now.csv (lệnh đề xuất từ nến mới nhất),
prices_1d.csv và prices_15m.csv (bộ nhớ đệm, dùng --use-cache để khỏi tải lại).

Lưu ý: chưa có bộ lọc Tầng 2 theo từng ngày (layer2 chỉ xuất phiên mới nhất) nên backtest là cận trên.
"""
from __future__ import annotations

import argparse
import importlib
import os
import re
import sys
import types
from datetime import date, timedelta

import pandas as pd

from tang4_atr_risk import (
    build_orders,
    daily_from_frame,
    load_universe,
    performance,
    run_backtest,
    signals_from_layer3,
)

# Các file tầng của nhóm gọi hàm chạy ngay ở cuối file (vd: layer3_results = run_layer3()).
# Khi import để dùng lại hàm, ta vô hiệu hoá các dòng đó để không kích hoạt lại cả tầng.
_AUTO_RUN = re.compile(r"^\s*\w+\s*=\s*(?:main|run_layer[123])\(\)\s*$", re.M)


def load_layer_module(path: str, name: str = "layer3_mod") -> types.ModuleType:
    if not os.path.isfile(path):
        sys.exit(f"Không thấy file Tầng 3: {path}")
    src = open(path, encoding="utf-8-sig").read()
    patched, n = _AUTO_RUN.subn("# (vô hiệu hoá khi import)", src)
    if n:
        print(f"      (đã bỏ {n} dòng tự chạy ở cuối {os.path.basename(path)}; "
              "nên bọc chúng trong `if __name__ == \"__main__\":`)")
    mod = types.ModuleType(name)
    mod.__file__ = path
    exec(compile(patched, path, "exec"), mod.__dict__)
    return mod


def fetch(dnse, symbols, resolution, start, end, cache, use_cache):
    if use_cache and os.path.isfile(cache):
        print(f"      dùng bộ nhớ đệm: {cache}")
        return pd.read_csv(cache)
    df = dnse.collect_prices(symbols, resolution=resolution, start=start, end=end, source="dnse", indexes=[])
    if df is None or df.empty:
        sys.exit(f"      LỖI: DNSE không trả dữ liệu {resolution}.")
    df.to_csv(cache, index=False, encoding="utf-8-sig")
    return df


def layer3_full(l3, raw_15m: pd.DataFrame) -> pd.DataFrame:
    """Chạy đúng chuỗi hàm của Tầng 3 nhưng giữ TẤT CẢ các nến (run_layer3 chỉ giữ nến cuối)."""
    df = l3.normalize_prices(raw_15m)
    df = l3.calculate_ema(df)
    df = l3.calculate_previous_close(df)
    df = l3.calculate_volume_ma20(df)
    return l3.calculate_momentum_score(df)


def main() -> None:
    ap = argparse.ArgumentParser(description="Chạy thử một lượt Tầng 3 -> Tầng 4 -> backtest")
    ap.add_argument("--dir", default=".", help="thư mục dự án (chứa DNSE.py, layer3.py, output/)")
    ap.add_argument("--layer3-file", default="layer3.py")
    ap.add_argument("--universe", default=None, help="mặc định: <dir>/output/tier1_pass_symbols.csv")
    ap.add_argument("--exclude-sectors", nargs="*", default=["BANK", "SECURITIES", "INSURANCE"])
    ap.add_argument("--days-15m", type=int, default=60, help="số ngày lịch nến 15 phút (Tầng 3 đang dùng 60)")
    ap.add_argument("--capital", type=float, default=100_000_000)
    ap.add_argument("--risk", type=float, default=0.01, help="0.01 = 1%% vốn mỗi lệnh")
    ap.add_argument("--use-cache", action="store_true")
    a = ap.parse_args()

    base = os.path.abspath(a.dir)
    sys.path.insert(0, base)
    out = os.path.join(base, "output", "tier4")
    os.makedirs(out, exist_ok=True)

    # ---------------------------------------------------------- [1] Universe + module
    print("[1/5] Nạp module và danh sách mã")
    try:
        dnse = importlib.import_module("DNSE")
    except ImportError as err:
        sys.exit(f"      LỖI: không import được DNSE.py trong {base} ({err})")
    l3 = load_layer_module(os.path.join(base, a.layer3_file))
    uni_path = a.universe or os.path.join(base, "output", "tier1_pass_symbols.csv")
    if not os.path.isfile(uni_path):
        sys.exit(f"      LỖI: không thấy {uni_path}. Chạy Tầng 1 trước.")
    symbols = load_universe(uni_path, exclude_sectors=a.exclude_sectors)
    print(f"      Universe: {len(symbols)} mã (đã loại ngành {a.exclude_sectors}): {', '.join(symbols)}")
    if not symbols:
        sys.exit("      LỖI: universe rỗng.")

    # ---------------------------------------------------------- [2] Dữ liệu
    print("[2/5] Lấy dữ liệu từ DNSE")
    end = date.today()
    start15 = end - timedelta(days=a.days_15m)
    start1d = start15 - timedelta(days=90)  # đủ >= 15 phiên trước tín hiệu sớm nhất để tính ATR14
    raw_1d = fetch(dnse, symbols, "1D", start1d, end, os.path.join(out, "prices_1d.csv"), a.use_cache)
    raw_15 = fetch(dnse, symbols, "15", start15, end, os.path.join(out, "prices_15m.csv"), a.use_cache)
    daily = daily_from_frame(raw_1d, symbols)
    if not daily:
        sys.exit("      LỖI: không mã nào có dữ liệu 1D.")
    bars = pd.Series({t: len(g) for t, g in daily.items()})
    print(f"      1D: {len(daily)}/{len(symbols)} mã | phiên/mã: min {bars.min()}, max {bars.max()}")
    r15 = raw_15.assign(time=pd.to_datetime(raw_15["time"]))
    print(f"      15 phút: {r15['symbol'].nunique()} mã | {r15['time'].min()} -> {r15['time'].max()}")
    sample = {t: round(float(g["close"].iloc[-1]), 2) for t, g in list(daily.items())[:3]}
    print(f"      Giá đóng cửa gần nhất (phải ở đơn vị đồng): {sample}")
    if pd.Series(sample).median() < 1000:
        print("      CẢNH BÁO: giá < 1.000 -> có thể là nghìn đồng. Sửa PRICE_MULTIPLIER trong DNSE.py "
              "rồi chạy lại KHÔNG dùng --use-cache.")
    missing = [s for s in symbols if s not in daily]
    if missing:
        print(f"      CẢNH BÁO: thiếu dữ liệu 1D: {missing}")

    # ---------------------------------------------------------- [3] Tầng 3 đầy đủ
    print("[3/5] Tính Tầng 3 trên toàn bộ nến 15 phút")
    full = layer3_full(l3, raw_15)
    print(f"      Nến đạt Tầng 3: {int(full['layer3_pass'].sum())}/{len(full)}")
    latest = full.sort_values("time").groupby("symbol").tail(1)
    print(f"      Đang BUY ở nến mới nhất ({latest['time'].max()}): {int(latest['layer3_pass'].sum())} mã")

    # ---------------------------------------------------------- [4] Lệnh đề xuất hiện tại
    print("[4/5] Lệnh Tầng 4 cho các mã đang BUY (chế độ chạy thật)")
    orders = build_orders(latest[latest["layer3_pass"]], daily, capital=a.capital, risk_pct=a.risk)
    if orders.empty:
        print("      Hiện không có mã BUY (hoặc chưa đủ dữ liệu ATR).")
    else:
        orders.to_csv(os.path.join(out, "orders_now.csv"), index=False, encoding="utf-8-sig")
        print(orders[["symbol", "entry_price", "atr14_daily", "stop_loss", "take_profit_4atr", "shares"]]
              .round(0).to_string(index=False))

    # ---------------------------------------------------------- [5] Backtest
    print("[5/5] Backtest trên lịch sử tín hiệu")
    sig = signals_from_layer3(full)
    print(f"      Số tín hiệu: {len(sig)} | số mã có tín hiệu: {sig['ticker'].nunique() if len(sig) else 0}")
    if sig.empty:
        sys.exit("      Không có tín hiệu trong lịch sử 15 phút đã tải.")
    trades = run_backtest(sig, daily, capital=a.capital, risk_pct=a.risk)
    if trades.empty:
        sys.exit("      Không có giao dịch: thiếu phiên daily ngày vào lệnh, chưa đủ 15 phiên tính ATR14, "
                 "hoặc số cổ phiếu = 0.")
    trades.to_csv(os.path.join(out, "trades.csv"), index=False, encoding="utf-8-sig")

    print("\n" + "=" * 70 + "\nKẾT QUẢ BACKTEST\n" + "=" * 70)
    print(trades["exit_reason"].value_counts().to_string())
    eod = int((trades["exit_reason"] == "End of data").sum())
    if eod:
        print(f"\nLưu ý: {eod}/{len(trades)} lệnh chưa thoát khi hết dữ liệu (tất toán theo giá đóng cửa cuối).")
    print()
    for k, v in performance(trades, a.capital).items():
        print(f"{k:<52}{v:>14.4f}" if isinstance(v, float) else f"{k:<52}{v:>14}")
    print(f"\nĐã lưu vào: {out}")


if __name__ == "__main__":
    main()
