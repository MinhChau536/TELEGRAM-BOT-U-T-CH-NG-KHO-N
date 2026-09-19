from __future__ import annotations
 
import argparse
import json
import logging
import math
import os
import random
import re
import sqlite3
import time
import unicodedata
import zlib
from datetime import date, datetime, timedelta, timezone
 
import pandas as pd
import requests
 
log = logging.getLogger("pipeline")
 
# ==============================================================================
# MỤC 1: CẤU HÌNH
# ==============================================================================
 
# [TODO 1]
DNSE_API_KEY = os.getenv("DNSE_API_KEY", "")
DNSE_SECRET_KEY = os.getenv("DNSE_SECRET_KEY", "")
 
# [TODO 2] URL xác thực: CHƯA ĐƯỢC XÁC MINH với tài liệu chính thức của DNSE.
URL_DNSE_AUTH = "https://services.entrade.com.vn/dnse-auth-service/v1/login"
 
# [TODO 3] Endpoint nến giá. Thử lần lượt từng URL cho đến khi có dữ liệu hợp lệ.
URL_DNSE_CHART_CANDIDATES = (
    "https://services.entrade.com.vn/chart-api/v2/ohlcs/stock",
    "https://services.entrade.com.vn/chart-api/v2/ohlcs",
)
 
# [TODO 4] Hệ số quy đổi đơn vị giá theo từng nguồn. Nếu nguồn trả giá theo NGHÌN ĐỒNG
# (vd 125.5 = 125,500đ) và muốn đơn vị ĐỒNG thì đặt 1000. Hãy in dữ liệu ra so với bảng điện.
PRICE_MULTIPLIER = {"DNSE": 1, "VNSTOCK": 1, "DEMO": 1}
 
DEFAULT_SYMBOLS = ["FPT", "VNM", "HPG", "VCB", "MWG"]   # sửa/thêm mã tùy ý (hoặc dùng --symbols)
 
TIMEOUT = 15          # giây
MAX_RETRIES = 3       # số lần thử lại khi lỗi mạng / lỗi server 5xx
RETRY_DELAY = 1.5     # giây (nhân với số lần thử)
REQUEST_PAUSE = 0.5   # giây nghỉ giữa các mã để tránh bị chặn vì gọi quá dày
 
VN_TZ = timezone(timedelta(hours=7))
VN_TZ_NAME = "Asia/Ho_Chi_Minh"
 
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}
 
# Khung thời gian hỗ trợ -> số giây của 1 nến ("1" = 1 phút)
BAR_SECONDS = {"1": 60, "5": 300, "15": 900, "30": 1800, "1H": 3600, "1D": 86400, "1W": 604800}
# Tên khung thời gian tương ứng của vnstock
VNSTOCK_INTERVALS = {"1": "1m", "5": "5m", "15": "15m", "30": "30m", "1H": "1H", "1D": "1D", "1W": "1W"}
 
# Format CHUẨN của dữ liệu giá: mọi nguồn đều phải đưa về đúng các cột này
STD_PRICE_COLUMNS = ["symbol", "resolution", "time", "open", "high", "low", "close", "volume", "source"]
 
 
def setup_logging(out_dir: str | None = None) -> None:
    """Ghi log ra màn hình và (nếu có thư mục) ra file pipeline.log."""
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        handlers.append(logging.FileHandler(os.path.join(out_dir, "pipeline.log"), encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
        force=True,
    )
 
 
# ==============================================================================
# MỤC 2: HÀM DÙNG CHUNG (KIỂM TRA ĐẦU VÀO, THỜI GIAN, HTTP, ÉP KIỂU)
# ==============================================================================
 
def validate_symbol(symbol: str) -> str:
    """Chuẩn hóa và kiểm tra mã cổ phiếu (3-10 ký tự chữ/số)."""
    if not isinstance(symbol, str):
        raise TypeError("Mã cổ phiếu phải là chuỗi (str)")
    symbol = symbol.upper().strip()
    if not re.fullmatch(r"[A-Z0-9]{3,10}", symbol):
        raise ValueError(f"Mã cổ phiếu không hợp lệ: '{symbol}'")
    return symbol
 
 
def parse_date(text: str) -> date:
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise ValueError(f"Ngày không hợp lệ '{text}'. Định dạng đúng: YYYY-MM-DD") from None
 
 
def _day_start_ts(d: date) -> int:
    """Unix timestamp của 00:00:00 (giờ VN) ngày d."""
    return int(datetime(d.year, d.month, d.day, tzinfo=VN_TZ).timestamp())
 
 
def _day_end_ts(d: date) -> int:
    return _day_start_ts(d) + 86399
 
 
def is_market_open(now: datetime | None = None) -> bool:
    """Phiên giao dịch cổ phiếu: T2-T6, 9:00-11:30 và 13:00-15:00 (giờ VN)."""
    now = now or datetime.now(VN_TZ)
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return 9 * 60 <= minutes < 11 * 60 + 30 or 13 * 60 <= minutes < 15 * 60
 
 
def _get_json_with_retry(url: str, params: dict, headers: dict) -> dict:
    """GET có thử lại khi lỗi mạng / lỗi server (5xx). Lỗi 4xx không thử lại."""
    last_err: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            res = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
            res.raise_for_status()
            return res.json()
        except requests.RequestException as err:
            last_err = err
            status = getattr(getattr(err, "response", None), "status_code", None)
            if status is not None and 400 <= status < 500:
                break  # lỗi phía client, thử lại vô ích
            time.sleep(RETRY_DELAY * attempt)
        except ValueError as err:  # phản hồi không phải JSON
            last_err = err
            break
    raise RuntimeError(f"Gọi API thất bại ({url}): {last_err}")
 
 
def _to_float(value, ndigits: int | None = None) -> float | None:
    """Ép về float; NaN / None / không phải số -> None (để JSON và SQLite hợp lệ)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return round(number, ndigits) if ndigits is not None else number
 
 
def _norm(text) -> str:
    """Bỏ dấu tiếng Việt, bỏ ký tự đặc biệt, viết thường: 'ROE (%)' -> 'roe'."""
    text = str(text).replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9]", "", text.lower())
 
 
# ==============================================================================
# MỤC 3: XÁC THỰC DNSE
# ==============================================================================
 
def build_auth_payload() -> dict:
    """[TODO 2] Tạo body gửi lên API xác thực. Sửa theo tài liệu DNSE."""
    return {"apiKey": DNSE_API_KEY, "secretKey": DNSE_SECRET_KEY}
 
 
def get_dnse_access_token() -> str:
    """Xác thực DNSE để lấy Bearer token.
    Trả về "" nếu chưa có key hoặc xác thực lỗi (chương trình vẫn chạy tiếp)."""
    if not (DNSE_API_KEY and DNSE_SECRET_KEY):
        log.info("Chưa gắn API Key DNSE -> dùng chế độ dữ liệu công khai.")
        return ""
    try:
        res = requests.post(URL_DNSE_AUTH, json=build_auth_payload(), headers=HEADERS, timeout=TIMEOUT)
        res.raise_for_status()
        body = res.json()
        token = body.get("accessToken") or body.get("access_token") or body.get("token") or ""
        if not token:
            raise ValueError("Phản hồi xác thực không chứa token")
        log.info("Đã xác thực DNSE thành công.")
        return token
    except (requests.RequestException, ValueError) as err:
        log.warning("Xác thực DNSE thất bại (%s) -> chuyển sang chế độ công khai.", err)
        return ""
 
 
# ==============================================================================
# MỤC 4: DỮ LIỆU GIÁ + KHỐI LƯỢNG (OHLCV): LÀM SẠCH & CHUẨN HÓA
# ==============================================================================
 
def standardize_ohlcv(
    df_raw: pd.DataFrame, symbol: str, resolution: str, source: str, time_is_unix: bool
) -> pd.DataFrame:
    """Làm sạch + chuẩn hóa dữ liệu nến của MỌI nguồn về 1 format duy nhất (STD_PRICE_COLUMNS).
 
    df_raw phải có các cột: time, open, high, low, close, volume.
    time_is_unix=True nếu time là Unix timestamp (giây, UTC) như DNSE; False nếu là ngày giờ.
    """
    # 1. Kiểm tra cấu trúc dữ liệu đầu vào
    required = ["time", "open", "high", "low", "close", "volume"]
    missing = [c for c in required if c not in df_raw.columns]
    if missing:
        raise ValueError(f"Thiếu cột dữ liệu: {missing}")
    if df_raw.empty:
        raise ValueError("Nguồn trả về dữ liệu rỗng (kiểm tra mã, khoảng ngày, giờ giao dịch)")
 
    df = df_raw[required].copy()
    rows_before = len(df)
 
    # 2. Ép kiểu số (lỗi -> NaN)
    numeric_cols = ["open", "high", "low", "close", "volume"]
    df[numeric_cols] = df[numeric_cols].apply(pd.to_numeric, errors="coerce")
 
    # 3. Chuẩn hóa thời gian về giờ Việt Nam, không kèm múi giờ
    if time_is_unix:
        stamps = pd.to_datetime(pd.to_numeric(df["time"], errors="coerce"), unit="s", utc=True)
        df["time"] = stamps.dt.tz_convert(VN_TZ_NAME).dt.tz_localize(None)
    else:
        stamps = pd.to_datetime(df["time"], errors="coerce")
        if stamps.dt.tz is not None:
            stamps = stamps.dt.tz_convert(VN_TZ_NAME).dt.tz_localize(None)
        df["time"] = stamps
 
    # 4. Loại dòng thiếu dữ liệu
    df = df.dropna()
 
    # 5. Loại dòng vô lý: giá <= 0, high < low, volume âm
    valid = (
        (df[["open", "high", "low", "close"]] > 0).all(axis=1)
        & (df["high"] >= df["low"])
        & (df["volume"] >= 0)
    )
    df = df[valid]
 
    # 6. Loại nến trùng thời gian, sắp xếp tăng dần
    df = df.drop_duplicates(subset="time", keep="last").sort_values("time").reset_index(drop=True)
    if df.empty:
        raise ValueError("Không còn dòng hợp lệ sau khi làm sạch dữ liệu")
    dropped = rows_before - len(df)
    if dropped > 0:
        log.info("%s [%s]: đã loại %d/%d dòng lỗi hoặc trùng lặp.", symbol, source, dropped, rows_before)
 
    # 7. Chuẩn hóa đơn vị + kiểu dữ liệu, thêm cột nhận diện
    price_cols = ["open", "high", "low", "close"]
    df[price_cols] = df[price_cols].astype("float64") * PRICE_MULTIPLIER.get(source, 1)
    df["volume"] = df["volume"].round().astype("int64")
    df["symbol"] = symbol
    df["resolution"] = resolution
    df["source"] = source
    return df[STD_PRICE_COLUMNS]
 
 
def fetch_prices_dnse(
    symbol: str, start: date, end: date, resolution: str, token: str = ""
) -> pd.DataFrame:
    """Gọi API DNSE lấy nến OHLCV trong khoảng [start, end] (lịch sử + nến mới nhất)."""
    from_ts = _day_start_ts(start)
    to_ts = min(_day_end_ts(end), int(time.time()))
    if to_ts <= from_ts:
        raise ValueError("Khoảng thời gian không hợp lệ (ngày bắt đầu phải trước thời điểm hiện tại)")
 
    params = {"from": from_ts, "to": to_ts, "symbol": symbol, "resolution": resolution}
    headers = dict(HEADERS)
    if token:
        headers["Authorization"] = f"Bearer {token}"
 
    errors: list[str] = []
    for url in URL_DNSE_CHART_CANDIDATES:
        try:
            payload = _get_json_with_retry(url, params, headers)
            data = payload.get("data", payload) if isinstance(payload, dict) else None
            if not isinstance(data, dict):
                raise ValueError("Dữ liệu trả về không phải dạng dict")
            keys = ["t", "o", "h", "l", "c", "v"]
            missing = [k for k in keys if k not in data]
            if missing:
                raise ValueError(f"Thiếu trường dữ liệu: {missing}")
            if len({len(data[k]) for k in keys}) != 1:
                raise ValueError("Các mảng t/o/h/l/c/v có độ dài khác nhau")
            raw = pd.DataFrame({
                "time": data["t"], "open": data["o"], "high": data["h"],
                "low": data["l"], "close": data["c"], "volume": data["v"],
            })
            return standardize_ohlcv(raw, symbol, resolution, "DNSE", time_is_unix=True)
        except (RuntimeError, ValueError) as err:
            errors.append(f"{url} -> {err}")
    raise RuntimeError("Không lấy được giá từ DNSE:\n    " + "\n    ".join(errors))
 
 
def fetch_prices_vnstock(symbol: str, start: date, end: date, resolution: str) -> pd.DataFrame:
    """Nguồn giá dự phòng (vnstock/VCI), dùng khi chưa có key DNSE hoặc DNSE lỗi."""
    interval = VNSTOCK_INTERVALS[resolution]
    end_exclusive = (end + timedelta(days=1)).isoformat()
    try:
        from vnstock.api.quote import Quote
    except ImportError:
        Quote = None  # noqa: N806 - bản vnstock cũ
 
    if Quote is not None:
        df = Quote(source="VCI", symbol=symbol).history(
            start=start.isoformat(), end=end_exclusive, interval=interval
        )
    else:
        from vnstock import Vnstock  # bản cũ
        df = Vnstock().stock(symbol=symbol, source="VCI").quote.history(
            start=start.isoformat(), end=end_exclusive, interval=interval
        )
 
    if df is None or df.empty:
        raise ValueError("vnstock trả về dữ liệu rỗng")
    # Đổi tên cột về dạng chuẩn (nguồn có thể đặt tên hơi khác nhau)
    rename = {}
    wanted = {
        "time": {"time", "date", "datetime", "tradingdate"},
        "open": {"open"}, "high": {"high"}, "low": {"low"},
        "close": {"close"}, "volume": {"volume", "vol"},
    }
    for col in df.columns:
        for std, names in wanted.items():
            if _norm(col) in names and std not in rename.values():
                rename[col] = std
    df = df.rename(columns=rename)
    return standardize_ohlcv(df, symbol, resolution, "VNSTOCK", time_is_unix=False)
 
 
# ---------- Dữ liệu GIẢ cho chế độ --demo (chạy thử pipeline, không cần mạng) ----------
 
def generate_demo_raw(symbol: str, start: date, end: date, resolution: str) -> pd.DataFrame:
    """Tạo nến GIẢ (xác định theo mã + thời điểm). Cố ý chèn vài dòng lỗi để thấy bước làm sạch."""
    step = BAR_SECONDS[resolution]
    base = 20 + (zlib.crc32(symbol.encode()) % 100)
    stamps: list[int] = []
    if resolution in ("1D", "1W"):
        d = start
        while d <= min(end, datetime.now(VN_TZ).date()):
            if d.weekday() < 5 and (resolution == "1D" or d.weekday() == 0):
                stamps.append(_day_start_ts(d))
            d += timedelta(days=1)
    else:
        end_ts = min(_day_end_ts(end), int(time.time()))
        first = max(_day_start_ts(start), end_ts - 300 * step)
        stamps = list(range((first // step + 1) * step, end_ts + 1, step))
 
    rows = []
    for t in stamps:
        rng = random.Random(zlib.crc32(f"{symbol}{t}".encode()))
        level = base * (1 + 0.08 * math.sin(t / 86400 / 9))
        o = level * (1 + rng.uniform(-0.004, 0.004))
        c = level * (1 + rng.uniform(-0.004, 0.004))
        h = max(o, c) * (1 + rng.uniform(0, 0.003))
        l = min(o, c) * (1 - rng.uniform(0, 0.003))
        rows.append([t, round(o, 2), round(h, 2), round(l, 2), round(c, 2), rng.randint(200_000, 1_000_000)])
    if len(rows) >= 6:  # chèn 3 dòng lỗi: trùng thời gian, thiếu giá đóng, high < low
        rows.append(list(rows[1]))
        t, o, h, l, c, v = rows[2]
        rows.append([t + 1, o, h, l, None, v])
        t, o, h, l, c, v = rows[3]
        rows.append([t + 2, o, l - 1, l, c, v])
    return pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])
 
 
def get_prices(
    symbol: str, start: date, end: date, resolution: str,
    token: str = "", source: str = "auto", demo: bool = False,
) -> tuple[pd.DataFrame | None, str | None, list[str]]:
    """Lấy giá theo thứ tự ưu tiên nguồn. Trả về (DataFrame chuẩn | None, nguồn đã dùng, danh sách lỗi)."""
    symbol = validate_symbol(symbol)
    if resolution not in BAR_SECONDS:
        raise ValueError(f"resolution '{resolution}' không hỗ trợ. Chọn: {list(BAR_SECONDS)}")
    if start > end:
        raise ValueError("Ngày bắt đầu phải nhỏ hơn hoặc bằng ngày kết thúc")
 
    if demo:
        raw = generate_demo_raw(symbol, start, end, resolution)
        return standardize_ohlcv(raw, symbol, resolution, "DEMO", time_is_unix=True), "DEMO", []
 
    order = {"dnse": ["DNSE"], "vnstock": ["VNSTOCK"], "auto": ["DNSE", "VNSTOCK"]}[source]
    errors: list[str] = []
    for src in order:
        try:
            if src == "DNSE":
                return fetch_prices_dnse(symbol, start, end, resolution, token), src, errors
            return fetch_prices_vnstock(symbol, start, end, resolution), src, errors
        except Exception as err:  # noqa: BLE001 - thư viện ngoài có thể ném nhiều loại lỗi
            errors.append(f"{src}: {err}")
    return None, None, errors
 
 
# ==============================================================================
# MỤC 5: CHỈ SỐ TÀI CHÍNH QUÝ / NĂM (VNSTOCK): LÀM SẠCH & CHUẨN HÓA
# ==============================================================================
 
METRICS = ["roe", "roa", "eps", "pe", "pb", "net_margin", "debt_to_equity"]
METRIC_DIGITS = {"roe": 4, "roa": 4, "eps": 2, "pe": 2, "pb": 2, "net_margin": 4, "debt_to_equity": 4}
 
# Tên cột thay đổi theo phiên bản vnstock / ngôn ngữ, nên khớp theo danh sách tên gọi
# (đã chuẩn hóa: bỏ dấu, ký tự đặc biệt, chữ thường). Thứ tự = độ ưu tiên.
METRIC_ALIASES = {
    "roe": ("roe",),
    "roa": ("roa",),
    "eps": ("eps", "epsvnd", "earningpershare", "earningspershare", "basiceps", "laicobantrencophieu"),
    "pe": ("pe", "peratio", "pricetoearning"),
    "pb": ("pb", "pbratio", "pricetobook"),
    "net_margin": ("aftertaxprofitmargin", "netmargin", "netprofitmargin", "bienlnsauthue",
                   "bienlnrong", "profitmargin"),
    # Nợ/Vốn chủ: vnstock có 2 cột gần giống nhau (Debt to Equity, Debt/Equity); ưu tiên "Debt to Equity".
    "debt_to_equity": ("debttoequity", "notrenvonchu", "debtperequity", "debtequity", "novonchu"),
}
 
 
def _keys_of(label) -> set[str]:
    """Các khóa chuẩn hóa của 1 tên cột (hỗ trợ cột nhiều cấp - MultiIndex)."""
    if isinstance(label, tuple):
        parts = [_norm(p) for p in label if str(p).strip() and not str(p).startswith("Unnamed")]
        return set(parts) | {"".join(parts)}
    return {_norm(label)}
 
 
def _find_column(df: pd.DataFrame, aliases):
    for alias in aliases:                      # duyệt theo thứ tự ưu tiên
        for col in df.columns:
            if alias in _keys_of(col):
                return col
    return None
 
 
def _column_values(df: pd.DataFrame, col) -> pd.Series:
    series = df[col]
    return series.iloc[:, 0] if isinstance(series, pd.DataFrame) else series
 
 
def _to_int(value) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None
 
 
def _period_sort_key(label: str) -> tuple[int, int]:
    """'2025-Q2' -> (2025, 2); '2025' -> (2025, 0); không đọc được -> (-1, -1)."""
    match = re.search(r"(\d{4})(?:\D*Q?(\d))?", str(label))
    return (-1, -1) if not match else (int(match.group(1)), int(match.group(2) or 0))
 
 
def _tidy_from_period_rows(df: pd.DataFrame, report_type: str) -> pd.DataFrame:
    """Bảng mà mỗi DÒNG là 1 kỳ báo cáo, mỗi CỘT là 1 chỉ số -> dạng trung gian."""
    tidy = pd.DataFrame(index=range(len(df)))
    for metric, aliases in METRIC_ALIASES.items():
        col = _find_column(df, aliases)
        tidy[metric] = (
            float("nan") if col is None
            else pd.to_numeric(_column_values(df, col), errors="coerce").values
        )
 
    y_col = _find_column(df, ("year", "nam", "yearreport"))
    q_col = _find_column(df, ("quarter", "quy", "lengthreport"))
    p_col = _find_column(df, ("period", "reportperiod", "kybaocao"))
    labels = []
    for i in range(len(df)):
        label = "N/A"
        if y_col is not None:
            year = _to_int(_column_values(df, y_col).iloc[i])
            quarter = _to_int(_column_values(df, q_col).iloc[i]) if q_col is not None else None
            if year is not None:
                is_q = report_type == "quarter" and quarter is not None and 1 <= quarter <= 4
                label = f"{year}-Q{quarter}" if is_q else str(year)
        elif p_col is not None:
            label = str(_column_values(df, p_col).iloc[i])
        labels.append(label)
    tidy.insert(0, "period", labels)
    return tidy
 
 
def _tidy_from_item_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Bảng đảo chiều: mỗi DÒNG là 1 chỉ số (cột item/item_en/item_id), mỗi CỘT là 1 kỳ."""
    meta = [c for c in df.columns if _norm(c) in {"item", "itemen", "itemid"}]
    period_cols = [c for c in df.columns if c not in meta]
    row_keys = [{_norm(row[c]) for c in meta if pd.notna(row[c])} for _, row in df.iterrows()]
    tidy = pd.DataFrame({"period": [str(c) for c in period_cols]})
    for metric, aliases in METRIC_ALIASES.items():
        tidy[metric] = float("nan")
        found = False
        for alias in aliases:
            for idx, keys in enumerate(row_keys):
                if alias in keys:
                    tidy[metric] = pd.to_numeric(df.iloc[idx][period_cols], errors="coerce").values
                    found = True
                    break
            if found:
                break
    return tidy
 
 
def _empty_bctc(symbol: str, report_type: str, error: str) -> dict:
    """Kết quả khi lỗi: CÙNG format với kết quả đúng, chỉ số = None (không phải 0)."""
    result = {
        "symbol": symbol,
        "period_type": "BÁO_CÁO_QUÝ" if report_type == "quarter" else "BÁO_CÁO_NĂM",
        "period_time": None,
    }
    result.update({m: None for m in METRICS})
    result.update({"status": "ERROR", "missing_metrics": list(METRICS), "error": error,
                   "source": "vnstock/VCI"})
    return result
 
 
def normalize_financial_ratio(df: pd.DataFrame, symbol: str, report_type: str) -> dict:
    """Chuẩn hóa bảng chỉ số tài chính (mọi dạng bảng) về 1 dict JSON thống nhất, kỳ mới nhất.
    Đơn vị ROE/ROA/biên LN được GIỮ NGUYÊN theo nguồn (in ra để kiểm tra 0.25 hay 25)."""
    if df is None or df.empty:
        return _empty_bctc(symbol, report_type, "Nguồn trả về bảng rỗng")
 
    is_item_rows = any(_norm(c) in {"itemid", "itemen"} for c in df.columns)
    tidy = _tidy_from_item_rows(df) if is_item_rows else _tidy_from_period_rows(df, report_type)
 
    # Kỳ mới nhất lên đầu (không phụ thuộc thứ tự nguồn trả về)
    order = sorted(range(len(tidy)), key=lambda i: _period_sort_key(tidy.loc[i, "period"]), reverse=True)
    tidy = tidy.iloc[order].reset_index(drop=True)
 
    has_data = tidy[METRICS].notna().any(axis=1)
    if not has_data.any():
        cols = ", ".join(map(str, df.columns))[:300]
        return _empty_bctc(symbol, report_type, f"Không nhận diện được cột chỉ số nào. Tên cột nguồn: {cols}")
    latest = tidy[has_data].iloc[0]   # kỳ mới nhất có ít nhất 1 chỉ số hợp lệ
 
    result = {
        "symbol": symbol,
        "period_type": "BÁO_CÁO_QUÝ" if report_type == "quarter" else "BÁO_CÁO_NĂM",
        "period_time": str(latest["period"]),
    }
    for metric in METRICS:
        result[metric] = _to_float(latest[metric], METRIC_DIGITS[metric])
    missing = [m for m in METRICS if result[m] is None]
    result.update({"status": "OK" if not missing else "PARTIAL", "missing_metrics": missing,
                   "error": None, "source": "vnstock/VCI"})
    return result
 
 
def _get_finance_client(symbol: str, period: str):
    """Ưu tiên API mới của vnstock; lớp cũ Vnstock().stock() chỉ là phương án dự phòng."""
    try:
        from vnstock.api.financial import Finance
    except ImportError:
        Finance = None  # noqa: N806
    if Finance is not None:
        return Finance(source="VCI", symbol=symbol, period=period)
    from vnstock import Vnstock  # bản cũ (hoặc gói vnstock3)
    return Vnstock().stock(symbol=symbol, source="VCI").finance
 
 
def generate_demo_ratio_frame(symbol: str, report_type: str) -> pd.DataFrame:
    """Bảng chỉ số GIẢ (giống dạng vnstock trả về) cho chế độ --demo."""
    rng = random.Random(zlib.crc32(f"{symbol}{report_type}".encode()))
    periods = [(2025, q) for q in (2, 1)] + [(2024, 4), (2024, 3)] if report_type == "quarter" \
        else [(2025, 5), (2024, 5), (2023, 5)]
    rows = [[y, q, rng.uniform(.08, .30), rng.uniform(.03, .15), rng.uniform(1500, 9000),
             rng.uniform(8, 30), rng.uniform(1, 6), rng.uniform(.05, .25), rng.uniform(.2, 2.5)]
            for y, q in periods]
    return pd.DataFrame(rows, columns=[
        "Year", "Quarter", "ROE (%)", "ROA (%)", "EPS (VND)", "P/E", "P/B",
        "After-tax Profit Margin (%)", "Debt to Equity"])
 
 
def get_financial_report(symbol: str, report_type: str = "quarter", demo: bool = False) -> dict:
    """Lấy chỉ số tài chính kỳ mới nhất: 'quarter' (quý) hoặc 'year' (năm)."""
    symbol = validate_symbol(symbol)
    if report_type not in ("quarter", "year"):
        raise ValueError("report_type phải là 'quarter' hoặc 'year'")
 
    if demo:
        result = normalize_financial_ratio(generate_demo_ratio_frame(symbol, report_type), symbol, report_type)
        result["source"] = "DEMO"
        return result
 
    try:
        client = _get_finance_client(symbol, report_type)
        df = client.ratio(period=report_type, lang="en", dropna=True)
        result = normalize_financial_ratio(df, symbol, report_type)
    except Exception as err:  # noqa: BLE001 - trả lỗi rõ ràng thay vì số 0 giả
        log.warning("Không lấy được chỉ số tài chính %s của %s: %s", report_type, symbol, err)
        return _empty_bctc(symbol, report_type, f"{type(err).__name__}: {err}")
 
    # EPS có thể không nằm trong bảng ratio -> thử lấy từ báo cáo kết quả kinh doanh (cố gắng tối đa)
    if result["status"] != "ERROR" and result["eps"] is None:
        try:
            df_is = client.income_statement(period=report_type, lang="en", dropna=True)
            res_is = normalize_financial_ratio(df_is, symbol, report_type)
            if res_is["eps"] is not None and res_is["period_time"] == result["period_time"]:
                result["eps"] = res_is["eps"]
                result["missing_metrics"] = [m for m in result["missing_metrics"] if m != "eps"]
                result["status"] = "OK" if not result["missing_metrics"] else "PARTIAL"
        except Exception as err:  # noqa: BLE001
            log.debug("Không lấy được EPS từ income_statement (%s): %s", symbol, err)
    return result
 
 
# ==============================================================================
# MỤC 6: LƯU TRỮ (SQLITE + CSV) VÀ BẢNG SÀNG LỌC
# ==============================================================================
 
SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    symbol TEXT NOT NULL, resolution TEXT NOT NULL, time TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL, volume INTEGER, source TEXT,
    PRIMARY KEY (symbol, resolution, time)
);
CREATE TABLE IF NOT EXISTS financials (
    symbol TEXT NOT NULL, period_type TEXT NOT NULL, period_time TEXT NOT NULL,
    roe REAL, roa REAL, eps REAL, pe REAL, pb REAL, net_margin REAL, debt_to_equity REAL,
    status TEXT, source TEXT, collected_at TEXT,
    PRIMARY KEY (symbol, period_type, period_time)
);
"""
 
 
def open_db(db_path: str) -> sqlite3.Connection:
    folder = os.path.dirname(os.path.abspath(db_path))
    os.makedirs(folder, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    return conn
 
 
def save_prices(conn: sqlite3.Connection, df: pd.DataFrame) -> int:
    """Ghi giá vào DB. Khóa (symbol, resolution, time) nên chạy lại không bị trùng (ghi đè)."""
    rows = [
        (r.symbol, r.resolution, r.time.strftime("%Y-%m-%d %H:%M:%S"),
         float(r.open), float(r.high), float(r.low), float(r.close), int(r.volume), r.source)
        for r in df.itertuples(index=False)
    ]
    conn.executemany("INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    return len(rows)
 
 
def save_financial(conn: sqlite3.Connection, record: dict) -> bool:
    """Ghi chỉ số tài chính (bỏ qua bản ghi lỗi hoàn toàn)."""
    if record["status"] == "ERROR" or not record["period_time"]:
        return False
    conn.execute(
        "INSERT OR REPLACE INTO financials VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (record["symbol"], record["period_type"], record["period_time"],
         *[record[m] for m in METRICS], record["status"], record["source"],
         datetime.now(VN_TZ).isoformat(timespec="seconds")),
    )
    conn.commit()
    return True
 
 
def build_screener_table(snapshots: list[dict]) -> pd.DataFrame:
    """Bảng phục vụ BỘ LỌC CƠ BẢN: mỗi mã 1 dòng, gồm giá/khối lượng mới nhất + chỉ số quý + chỉ số năm."""
    rows = []
    for snap in snapshots:
        last = snap["market_data"]["last_candle"] or {}
        row = {
            "symbol": snap["symbol"],
            "price_source": snap["market_data"]["source"],
            "last_time": last.get("time"),
            "last_close": last.get("close"),
            "last_volume": last.get("volume"),
        }
        for prefix, key in (("q_", "quarter"), ("y_", "year")):
            fin = snap["financials"][key]
            row[prefix + "period"] = fin["period_time"]
            for m in METRICS:
                row[prefix + m] = fin[m]
        rows.append(row)
    return pd.DataFrame(rows)
 
 
# ==============================================================================
# MỤC 7: PIPELINE (LỊCH SỬ + BCTC) VÀ CHẾ ĐỘ THỜI GIAN THỰC
# ==============================================================================
 
def run_pipeline(
    symbols: list[str], start: date, end: date, resolution: str, price_source: str,
    db_path: str, out_dir: str, demo: bool, pause: float,
) -> list[dict]:
    """Thu thập -> làm sạch -> chuẩn hóa -> lưu (SQLite + CSV) cho danh sách mã."""
    os.makedirs(out_dir, exist_ok=True)
    token = "" if demo else get_dnse_access_token()
    conn = open_db(db_path)
    snapshots: list[dict] = []
    all_prices: list[pd.DataFrame] = []
 
    for i, raw_symbol in enumerate(symbols):
        symbol = validate_symbol(raw_symbol)
        log.info("[%d/%d] Đang xử lý %s ...", i + 1, len(symbols), symbol)
        errors: list[str] = []
 
        df_price, used_source, price_errors = get_prices(
            symbol, start, end, resolution, token, price_source, demo)
        errors.extend(price_errors)
        last_candle, n_bars = None, 0
        if df_price is not None:
            n_bars = save_prices(conn, df_price)
            all_prices.append(df_price)
            last = df_price.iloc[-1]
            last_candle = {"time": last["time"].strftime("%Y-%m-%d %H:%M:%S"), "open": last["open"],
                           "high": last["high"], "low": last["low"], "close": last["close"],
                           "volume": int(last["volume"])}
        else:
            log.warning("%s: không lấy được giá từ nguồn nào.", symbol)
 
        fin_q = get_financial_report(symbol, "quarter", demo)
        fin_y = get_financial_report(symbol, "year", demo)
        for fin in (fin_q, fin_y):
            save_financial(conn, fin)
            if fin["status"] == "ERROR":
                errors.append(f"Chỉ số tài chính {fin['period_type']}: {fin['error']}")
 
        snapshots.append({
            "symbol": symbol,
            "collected_at": datetime.now(VN_TZ).isoformat(timespec="seconds"),
            "market_data": {"source": used_source, "resolution": resolution,
                            "rows": n_bars, "last_candle": last_candle},
            "financials": {"quarter": fin_q, "year": fin_y},
            "errors": errors,
        })
        if i < len(symbols) - 1 and pause > 0:
            time.sleep(pause)
    conn.close()
 
    # Xuất file
    if all_prices:
        pd.concat(all_prices, ignore_index=True).to_csv(
            os.path.join(out_dir, f"prices_{resolution}.csv"), index=False, encoding="utf-8-sig")
    screener = build_screener_table(snapshots)
    screener.to_csv(os.path.join(out_dir, "screener_table.csv"), index=False, encoding="utf-8-sig")
    with open(os.path.join(out_dir, "snapshot.json"), "w", encoding="utf-8") as fh:
        json.dump(snapshots, fh, indent=2, ensure_ascii=False, default=str)
    return snapshots
 
 
def run_realtime(
    symbols: list[str], resolution: str, price_source: str, db_path: str,
    interval: int, iterations: int | None, demo: bool,
) -> None:
    """Chế độ GẦN THỜI GIAN THỰC: định kỳ gọi lại API lấy nến mới nhất, ghi vào DB.
    (API nến không phải luồng từng lệnh; muốn tick thật cần MQTT/WebSocket của DNSE.)"""
    token = "" if demo else get_dnse_access_token()
    conn = open_db(db_path)
    n = 0
    log.info("Bắt đầu theo dõi %s, khung %s, cập nhật mỗi %ds. Nhấn Ctrl+C để dừng.",
             ", ".join(symbols), resolution, interval)
    try:
        while iterations is None or n < iterations:
            n += 1
            if not demo and not is_market_open():
                log.info("Ngoài giờ giao dịch (T2-T6, 9:00-11:30 và 13:00-15:00): sẽ hiển thị nến gần nhất.")
            today = datetime.now(VN_TZ).date()
            for raw_symbol in symbols:
                symbol = validate_symbol(raw_symbol)
                df, used, errs = get_prices(symbol, today, today, resolution, token, price_source, demo)
                if df is None:
                    log.warning("%s: chưa có dữ liệu (%s)", symbol, "; ".join(errs)[:200])
                    continue
                save_prices(conn, df)
                last = df.iloc[-1]
                print(f"[{datetime.now(VN_TZ):%H:%M:%S}] {symbol:<6} nến {last['time']:%Y-%m-%d %H:%M} | "
                      f"giá đóng {last['close']:>10,.2f} | KL {int(last['volume']):>10,} | nguồn {used}")
            if iterations is None or n < iterations:
                time.sleep(interval)
    except KeyboardInterrupt:
        log.info("Đã dừng theo yêu cầu.")
    finally:
        conn.close()
 
 
# ==============================================================================
# MỤC 8: DÒNG LỆNH
# ==============================================================================
 
def print_summary(snapshots: list[dict]) -> None:
    print("\n" + "=" * 96)
    print("  TÓM TẮT KẾT QUẢ")
    print("=" * 96)
    print(f"{'Mã':<7}{'Nguồn giá':<11}{'Số nến':>7}{'Giá đóng cuối':>15}{'Kỳ quý':>10}{'TT quý':>9}{'Kỳ năm':>8}{'TT năm':>9}")
    for s in snapshots:
        md, q, y = s["market_data"], s["financials"]["quarter"], s["financials"]["year"]
        close = md["last_candle"]["close"] if md["last_candle"] else None
        print(f"{s['symbol']:<7}{str(md['source'] or '-'):<11}{md['rows']:>7}"
              f"{(f'{close:,.2f}' if close is not None else '-'):>15}"
              f"{str(q['period_time'] or '-'):>10}{q['status']:>9}"
              f"{str(y['period_time'] or '-'):>8}{y['status']:>9}")
    failed = [s for s in snapshots if s["errors"]]
    if failed:
        print("\nLỖI GHI NHẬN:")
        for s in failed:
            for e in s["errors"]:
                print(f"  * {s['symbol']}: {e}")
 
 
def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Pipeline dữ liệu chứng khoán VN (DNSE + vnstock)")
    p.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS, help="Danh sách mã, vd: FPT VNM HPG")
    p.add_argument("--start", help="Ngày bắt đầu YYYY-MM-DD (mặc định: 180 ngày trước, hoặc hôm nay với nến trong ngày)")
    p.add_argument("--end", help="Ngày kết thúc YYYY-MM-DD (mặc định: hôm nay)")
    p.add_argument("--resolution", default="1D", choices=list(BAR_SECONDS), help="Khung nến (mặc định 1D)")
    p.add_argument("--price-source", default="auto", choices=["auto", "dnse", "vnstock"],
                   help="auto = thử DNSE rồi vnstock")
    p.add_argument("--realtime", action="store_true", help="Chế độ theo dõi gần thời gian thực")
    p.add_argument("--interval", type=int, default=60, help="Giây giữa 2 lần cập nhật (realtime)")
    p.add_argument("--iterations", type=int, default=None, help="Số lần cập nhật (mặc định: chạy đến khi Ctrl+C)")
    p.add_argument("--db", help="Đường dẫn file SQLite (mặc định: <out-dir>/market_data.db)")
    p.add_argument("--out-dir", help="Thư mục xuất file (mặc định: output, hoặc demo_output khi --demo)")
    p.add_argument("--demo", action="store_true", help="Chạy thử bằng dữ liệu GIẢ, không cần mạng/key")
    p.add_argument("--pause", type=float, default=REQUEST_PAUSE, help="Giây nghỉ giữa các mã")
    return p
 
 
def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    out_dir = args.out_dir or ("demo_output" if args.demo else "output")
    db_path = args.db or os.path.join(out_dir, "market_data.db")
    setup_logging(out_dir)
 
    if args.demo:
        log.warning("CHẾ ĐỘ DEMO: dữ liệu là GIẢ (nguồn 'DEMO'), chỉ dùng để kiểm tra pipeline.")
 
    try:
        symbols = [validate_symbol(s) for s in args.symbols]
        today = datetime.now(VN_TZ).date()
        end = parse_date(args.end) if args.end else today
        if args.start:
            start = parse_date(args.start)
        else:
            start = end - timedelta(days=180) if args.resolution in ("1D", "1W") else end
        if start > end:
            raise ValueError("--start phải nhỏ hơn hoặc bằng --end")
    except (ValueError, TypeError) as err:
        log.error("Tham số không hợp lệ: %s", err)
        return 2
 
    if args.realtime:
        resolution = args.resolution if args.resolution not in ("1D", "1W") else "1"
        run_realtime(symbols, resolution, args.price_source, db_path, args.interval, args.iterations, args.demo)
        return 0
 
    snapshots = run_pipeline(symbols, start, end, args.resolution, args.price_source,
                             db_path, out_dir, args.demo, args.pause)
    print_summary(snapshots)
    candidates = [db_path, os.path.join(out_dir, f"prices_{args.resolution}.csv"),
                  os.path.join(out_dir, "screener_table.csv"), os.path.join(out_dir, "snapshot.json"),
                  os.path.join(out_dir, "pipeline.log")]
    print("\nFile đã tạo:", " | ".join(f for f in candidates if os.path.exists(f)))
    if not any(s["market_data"]["rows"] for s in snapshots):
        print("LƯU Ý: không lấy được dòng giá nào -> không có file prices_*.csv. Xem mục 'LỖI GHI NHẬN' ở trên.")
    return 0
 
 
if __name__ == "__main__":
    raise SystemExit(main())