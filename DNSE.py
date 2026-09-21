from __future__ import annotations

import logging
import os
import re
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests

log = logging.getLogger("api")

# ==============================================================================
# CẤU HÌNH
# ==============================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DNSE_API_KEY = os.getenv("DNSE_API_KEY", "eyJvcmciOiJkbnNlIiwiaWQiOiI1ZWVjYzY0YTY5ZjI0ZmE5YTU1ODMwM2Y5ZjhiMzVkMiIsImgiOiJtdXJtdXIxMjgifQ==")
DNSE_SECRET_KEY = os.getenv("DNSE_SECRET_KEY", "IUPiXi7W5Xl2fDIKrU6HpmrGJGHwWIWwwwYVemi5yx2Hlsz4E5RQoZYWHYjKfJICImfeokRO1n8Je-FGvGUEXg")
URL_DNSE_AUTH = "https://services.entrade.com.vn/dnse-auth-service/v1/login"          # CHƯA xác minh
URL_DNSE_CHART_STOCK = (                                                              # CHƯA xác minh, thử lần lượt
    "https://services.entrade.com.vn/chart-api/v2/ohlcs/stock",
    "https://services.entrade.com.vn/chart-api/v2/ohlcs",
)
URL_DNSE_CHART_INDEX = ("https://services.entrade.com.vn/chart-api/v2/ohlcs/index",)  # CHƯA xác minh

# >>> ĐƯỜNG DẪN CSV BCTC DỰ PHÒNG: bù kỳ / chỉ số mà vnstock thiếu <<<
FINANCIAL_CSV_PATH = os.getenv(
    "FINANCIAL_CSV_PATH",
    os.path.join(BASE_DIR, "/Users/thylnh.iu/Documents/Gói phần mềm ứng dụng cho tài chính 1/Filedata.csv"),
)

INDEX_SYMBOLS = {"VNINDEX", "VN30", "HNXINDEX", "HNX30", "UPCOMINDEX"}   # mã chỉ số (không nhân PRICE_MULTIPLIER)
DEFAULT_INDEXES = ["VNINDEX"]

DEFAULT_SYMBOLS = [
    # --- 1. Ngân hàng (22 mã) ---
    "VCB", "BID", "CTG", "TCB", "MBB", "ACB", "VPB", "STB", "HDB", "TPB",
    "LPB", "SHB", "MSB", "VIB", "EIB", "SSB", "OCB", "BAB", "ABB", "NAB",
    "SGB", "BVB",

    # --- 2. Chứng khoán (23 mã) ---
    "SSI", "VCI", "HCM", "VND", "MBS", "SHS", "FTS", "CTS", "BSI", "AGR",
    "ORS", "VDS", "BVS", "IVS", "TCI", "VIX", "SBS", "TVB", "PSI", "WSS",
    "APG", "EIV", "HBS",

    # --- 3. Bất động sản Dân dụng & Khu công nghiệp (45 mã) ---
    "VIC", "VHM", "VRE", "NVL", "KDH", "NLG", "PDR", "DXG", "DIG", "CEO",
    "KBC", "IDC", "SZC", "ITA", "SIP", "LHGP", "BCM", "TCH", "HDG", "NTH",
    "CRE", "KHG", "DXS", "HQC", "SCR", "LDG", "CIIC", "NBB", "IJC", "AGG",
    "HDC", "D2D", "NTC", "TIP", "VRG", "DRH", "SJS", "L14", "VC3", "IDV",
    "DTG", "TDH", "ITC", "STL", "CCL",

    # --- 4. Thép, Vật liệu xây dựng & Hóa chất (30 mã) ---
    "HPG", "HSG", "NKG", "SMC", "TLH", "POM", "VGS", "DGC", "DCM", "DPM",
    "CSV", "BFC", "LAS", "PLC", "HT1", "BCC", "BMP", "NTP", "DPR", "PHR",
    "GVR", "DRI", "TNC", "VIS", "HLA", "KHB", "HOM", "C32", "DHA", "LBM",

    # --- 5. Bán lẻ, Tiêu dùng, Nông nghiệp & Đồ uống (35 mã) ---
    "MWG", "FRT", "PNJ", "MSN", "VNM", "SAB", "BHN", "DGW", "HAX", "PET",
    "KDC", "MCH", "QNS", "TLG", "DBC", "BAF", "HNG", "HAG", "PAN", "LTG",
    "VCF", "SBT", "SLS", "LSS", "MCM", "CLM", "TAR", "BFN", "BBC", "NAF",
    "VHE", "TSC", "SJ1", "APF", "LIX",

    # --- 6. Công nghệ, Viễn thông & Vận tải, Logistics, Cảng biển (35 mã) ---
    "FPT", "CMG", "ELC", "CTR", "FOX", "VGI", "SGP", "GMD", "HAH", "VSC",
    "VJC", "HVN", "AST", "ACV", "TMS", "ILB", "SFI", "SAD", "PVT", "PVP",
    "VIP", "VTO", "SCT", "PDN", "CLL", "TCO", "PJT", "ITD", "SAM", "TTN",
    "VTC", "SGT", "SRT", "HRT", "TTV",

    # --- 7. Dầu khí, Năng lượng, Điện & Nước (35 mã) ---
    "GAS", "PLX", "POW", "PVD", "PVS", "PVC", "PVB", "BSR", "OIL", "REE",
    "PC1", "GEG", "TTA", "NT2", "QTP", "HND", "SJD", "VSH", "SNC", "PGD",
    "CNG", "ASP", "PGS", "PVG", "TV4", "TBC", "VPD", "SJD", "SEB", "NED",
    "BTP", "PPA", "DNW", "TDW", "TDM",

    # --- 8. Dệt may, Thủy sản, Gỗ & Cao su tự nhiên (30 mã) ---
    "VHC", "ANV", "IDI", "FMC", "MPC", "CMX", "TNG", "MSH", "STK", "GIL",
    "TTC", "GDT", "VGT", "TCM", "VTK", "ADS", "MDT", "HTG", "AAM", "ACL",
    "BLF", "SSN", "PTB", "TTF", "GTA", "ACG", "TRC", "RTB", "HRC", "DRI",

    # --- 9. Xây dựng, Đầu tư công & Hạ tầng (30 mã) ---
    "VCG", "HHV", "C4G", "FCN", "LCG", "CTD", "HBC", "DCN", "DTI", "KSB",
    "SCN", "G36", "TV2", "HID", "EVG", "HUB", "LIG", "VNE", "TBD", "TCD",
    "BOT", "C92", "PNE", "KPF", "S99", "SCI", "HDA", "PXI", "PXS", "L40",

    # --- 10. Dược phẩm, Y tế & Khác (15 mã) ---
    "DHG", "IMP", "TRA", "DBD", "DMC", "DCL", "PMC", "AMV", "JVC", "FIT",
    "RAL", "VMD", "LDP", "SPM", "DP3"
]
TIMEOUT, MAX_RETRIES, RETRY_DELAY, REQUEST_PAUSE = 15, 3, 1.5, 3.5
COLLECT_RESOLUTION = "15"        # nến 15 phút (quyết định của nhóm)
CANDLE_CLOSE_DELAY = 20          # giây chờ sau khi nến đóng rồi mới gọi API
WARMUP_DAYS = 7                  # số ngày tải lùi cho EMA50, RSI14, ATR14...
TOKEN_REFRESH_SECONDS = 4 * 3600
BAR_TIME_IS_START = True         # CHƯA xác minh: 't' của DNSE là giờ BẮT ĐẦU nến. Nếu là giờ kết thúc -> False
PRICE_MULTIPLIER = {"DNSE": 1, "VNSTOCK": 1}   # đặt 1000 nếu nguồn trả giá theo nghìn đồng; 2 nguồn phải CÙNG đơn vị
MAX_FINANCE_PERIODS = 10
VNSTOCK_CALL_INTERVAL = 3.5      # giãn cách tối thiểu giữa các lần gọi vnstock để không dính rate limit 20 req/phút
VNSTOCK_RETRIES = 3

VN_TZ = timezone(timedelta(hours=7))
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"}
BAR_SECONDS = {"1": 60, "5": 300, "15": 900, "30": 1800, "1H": 3600, "1D": 86400, "1W": 604800}
VNSTOCK_INTERVALS = {"1": "1m", "5": "5m", "15": "15m", "30": "30m", "1H": "1H", "1D": "1D", "1W": "1W"}

# ---- Format CHUẨN ----
PRICE_COLUMNS = ["symbol", "resolution", "time", "open", "high", "low", "close", "volume", "source"]
METRICS = ["roe", "roa", "eps", "pe", "pb", "net_margin", "debt_to_equity", "profit_after_tax"]
FIN_COLUMNS = ["symbol", "period_type", "period", *METRICS, "status", "source"]
METRIC_DIGITS = {"roe": 4, "roa": 4, "eps": 2, "pe": 2, "pb": 2,
                 "net_margin": 4, "debt_to_equity": 4, "profit_after_tax": 2}
KNOWN_SOURCES = {"DNSE", "VNSTOCK", "vnstock", "csv", "vnstock+csv", "none"}

METRIC_ALIASES = {
    "roe": ("roe", "returnonequity", "tss"),
    "roa": ("roa", "returnonassets", "tst"),
    "eps": (
        "eps", "epsvnd", "earningpershare", "earningspershare", "basiceps",
        "laicobantrencophieu", "laicobantrencophieuvnd", "lãi cơ bản trên cổ phiếu",
        "lailotrencophieu", "lailocobantrencophieu", "lailotrencohieu"
    ),
    "pe": ("pe", "peratio", "pricetoearning", "giaquacophieu", "priceearningsratio"),
    "pb": ("pb", "pbratio", "pricetobook", "giatrongso"),
    "net_margin": (
        "aftertaxprofitmargin", "aftertaxmargin", "netmargin", "netprofitmargin",
        "bienlnsauthue", "bienlnrong", "profitmargin", "loinhuansauthuetrencdoanhthu",
        "loinhuansauthuetrenndoanhthu", "loinhuansauthue", "loinhuansauthuenetrong", "loinhuanrong",
        "after_tax_margin"
    ),
    "debt_to_equity": ("debttoequity", "notrenvonchu", "debtperequity", "debtequity", "novonchu", "nợvốnchu"),
    "profit_after_tax": (
        "aftertaxprofit", "aftertaxnetprofit", "netprofitaftertax", "profitaftertax", "netincome",
        "netincomeaftertax", "profitaftertaxvnd", "loinhuansauthue", "loinhuansauthuevnd",
        "lailothuansauthue", "lailothuansauthuevnd", "loinhuansauthue", "lailoaftertax",
        "earningsaftertax", "incomeaftertax", "ketquasauthue", "loinhuansauthu"
    ),
}
YEAR_KEYS, QUARTER_KEYS, PERIOD_KEYS = ("year", "nam", "yearreport"), ("quarter", "quy", "lengthreport", "quarterreport"), ("period", "reportperiod", "kybaocao")
TICKER_KEYS = ("ticker", "symbol", "ma")


def setup_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(level=level, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S", force=True)


# ==============================================================================
# HÀM DÙNG CHUNG
# ==============================================================================
_last_request_ts = 0.0


def _rate_limit_sleep(min_gap: float = REQUEST_PAUSE) -> None:
    """Giữ khoảng nghỉ tối thiểu giữa các request, không làm ảnh hưởng lịch thu thập 15 phút."""
    global _last_request_ts
    now = time.monotonic()
    if _last_request_ts:
        wait = min_gap - (now - _last_request_ts)
        if wait > 0:
            time.sleep(wait)
    _last_request_ts = time.monotonic()


def validate_symbol(symbol: str) -> str:
    symbol = str(symbol).upper().strip()
    if not re.fullmatch(r"[A-Z0-9]{3,10}", symbol):
        raise ValueError(f"Mã không hợp lệ: '{symbol}'")
    return symbol


def _norm(text) -> str:
    text = unicodedata.normalize("NFD", str(text).replace("đ", "d").replace("Đ", "D"))
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in text if unicodedata.category(c) != "Mn").lower())


def _get_json(url: str, params: dict, headers: dict) -> dict:
    _rate_limit_sleep(REQUEST_PAUSE)
    last: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            res = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
            res.raise_for_status()
            return res.json()
        except requests.RequestException as err:
            last = err
            status = getattr(err.response, "status_code", None)
            if status is not None and 400 <= status < 500:
                break
            time.sleep(RETRY_DELAY * attempt)
        except ValueError as err:
            last = err
            break
    raise RuntimeError(f"Gọi API thất bại ({url}): {last}")


def save_csv(df: pd.DataFrame, path: str, keys: list[str]) -> int:
    if df.empty:
        return 0
    df = df.copy()
    for col in df.select_dtypes("datetime").columns:
        df[col] = df[col].dt.strftime("%Y-%m-%d %H:%M:%S")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    if os.path.exists(path):
        df = pd.concat([pd.read_csv(path, dtype={k: str for k in keys}), df], ignore_index=True)
    df = df.drop_duplicates(subset=keys, keep="last").sort_values(keys)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return len(df)


# ==============================================================================
# GIÁ: DNSE (chính) + vnstock (dự phòng)
# ==============================================================================
_token = {"value": "", "at": 0.0}


def dnse_token() -> str:
    if not DNSE_API_KEY:
        return ""
    if _token["value"] and time.time() - _token["at"] < TOKEN_REFRESH_SECONDS:
        return _token["value"]
    _rate_limit_sleep(REQUEST_PAUSE)
    payload = {"apiKey": DNSE_API_KEY, **({"secretKey": DNSE_SECRET_KEY} if DNSE_SECRET_KEY else {})}
    try:
        res = requests.post(URL_DNSE_AUTH, json=payload, headers=HEADERS, timeout=TIMEOUT)
        res.raise_for_status()
        body = res.json()
        token = body.get("accessToken") or body.get("access_token") or body.get("token") or ""
        if not token:
            raise ValueError("phản hồi không chứa token")
        _token.update(value=token, at=time.time())
        return token
    except (requests.RequestException, ValueError) as err:
        log.warning("Xác thực DNSE thất bại (%s) -> gọi dữ liệu công khai.", err)
        return ""


def standardize_ohlcv(raw: pd.DataFrame, symbol: str, resolution: str, source: str, time_is_unix: bool) -> pd.DataFrame:
    need = ["time", "open", "high", "low", "close", "volume"]
    missing = [c for c in need if c not in raw.columns]
    if missing:
        raise ValueError(f"Thiếu cột dữ liệu: {missing}")
    df = raw[need].copy()
    df[need[1:]] = df[need[1:]].apply(pd.to_numeric, errors="coerce")
    if time_is_unix:
        stamps = pd.to_datetime(pd.to_numeric(df["time"], errors="coerce"), unit="s", utc=True)
        df["time"] = stamps.dt.tz_convert("Asia/Ho_Chi_Minh").dt.tz_localize(None)
    else:
        stamps = pd.to_datetime(df["time"], errors="coerce")
        df["time"] = stamps.dt.tz_convert("Asia/Ho_Chi_Minh").dt.tz_localize(None) if stamps.dt.tz is not None else stamps
    df = df.dropna()
    ok = (df[["open", "high", "low", "close"]] > 0).all(axis=1) & (df["high"] >= df["low"]) & (df["volume"] >= 0)
    df = df[ok].drop_duplicates("time", keep="last").sort_values("time").reset_index(drop=True)
    if df.empty:
        raise ValueError("Không còn dòng hợp lệ sau khi làm sạch")
    prices = ["open", "high", "low", "close"]
    mult = 1 if symbol in INDEX_SYMBOLS else PRICE_MULTIPLIER.get(source, 1)
    df[prices] = df[prices].astype("float64") * mult
    df["volume"] = df["volume"].round().astype("int64")
    df["symbol"], df["resolution"], df["source"] = symbol, resolution, source
    return df[PRICE_COLUMNS]


def drop_unclosed_candles(df: pd.DataFrame, resolution: str) -> pd.DataFrame:
    if resolution in ("1D", "1W"):
        return df
    now = datetime.now(VN_TZ).replace(tzinfo=None)
    end_time = df["time"] + timedelta(seconds=BAR_SECONDS[resolution]) if BAR_TIME_IS_START else df["time"]
    return df[end_time <= now].reset_index(drop=True)


def fetch_prices_dnse(symbol: str, start: date, end: date, resolution: str) -> pd.DataFrame:
    day0 = lambda d: int(datetime(d.year, d.month, d.day, tzinfo=VN_TZ).timestamp())  # noqa: E731
    from_ts, to_ts = day0(start), min(day0(end) + 86399, int(time.time()))
    if to_ts <= from_ts:
        raise ValueError("Khoảng thời gian không hợp lệ")
    params = {"from": from_ts, "to": to_ts, "symbol": symbol, "resolution": resolution}
    headers = dict(HEADERS)
    if token := dnse_token():
        headers["Authorization"] = f"Bearer {token}"
    urls = URL_DNSE_CHART_INDEX + URL_DNSE_CHART_STOCK if symbol in INDEX_SYMBOLS else URL_DNSE_CHART_STOCK
    keys, errors = ["t", "o", "h", "l", "c", "v"], []
    for url in urls:
        try:
            payload = _get_json(url, params, headers)
            data = payload.get("data", payload) if isinstance(payload, dict) else None
            if not isinstance(data, dict) or [k for k in keys if k not in data]:
                raise ValueError("thiếu trường t/o/h/l/c/v")
            if len({len(data[k]) for k in keys}) != 1:
                raise ValueError("các mảng t/o/h/l/c/v khác độ dài")
            raw = pd.DataFrame({"time": data["t"], "open": data["o"], "high": data["h"],
                                "low": data["l"], "close": data["c"], "volume": data["v"]})
            return standardize_ohlcv(raw, symbol, resolution, "DNSE", time_is_unix=True)
        except (RuntimeError, ValueError) as err:
            errors.append(f"{url} -> {err}")
    raise RuntimeError(" | ".join(errors))


def fetch_prices_vnstock(symbol: str, start: date, end: date, resolution: str) -> pd.DataFrame:
    _rate_limit_sleep(VNSTOCK_CALL_INTERVAL)
    kwargs = {"start": start.isoformat(), "end": (end + timedelta(days=1)).isoformat(), "interval": VNSTOCK_INTERVALS[resolution]}
    try:
        from vnstock.api.quote import Quote
        df = Quote(source="VCI", symbol=symbol).history(**kwargs)
    except ImportError:
        from vnstock import Vnstock
        df = Vnstock().stock(symbol=symbol, source="VCI").quote.history(**kwargs)
    if df is None or df.empty:
        raise ValueError("vnstock trả về dữ liệu rỗng")
    names = {"time": {"time", "date", "datetime", "tradingdate"}, "open": {"open"}, "high": {"high"},
             "low": {"low"}, "close": {"close"}, "volume": {"volume", "vol"}}
    rename = {}
    for col in df.columns:
        for std, alias in names.items():
            if _norm(col) in alias and std not in rename.values():
                rename[col] = std
    return standardize_ohlcv(df.rename(columns=rename), symbol, resolution, "VNSTOCK", time_is_unix=False)


def fetch_prices(symbol: str, start: date, end: date, resolution: str, closed_only: bool = True,
                 source: str = "auto") -> pd.DataFrame:
    symbol = validate_symbol(symbol)
    if resolution not in BAR_SECONDS:
        raise ValueError(f"resolution '{resolution}' không hỗ trợ. Chọn: {list(BAR_SECONDS)}")
    if start > end:
        raise ValueError("Ngày bắt đầu phải <= ngày kết thúc")
    errors = []
    for src in {"dnse": ["DNSE"], "vnstock": ["VNSTOCK"], "auto": ["DNSE", "VNSTOCK"]}[source]:
        try:
            df = fetch_prices_dnse(symbol, start, end, resolution) if src == "DNSE" \
                else fetch_prices_vnstock(symbol, start, end, resolution)
            return drop_unclosed_candles(df, resolution) if closed_only else df
        except (BaseException) as err:
            errors.append(f"{src}: {err}")
    raise RuntimeError(" || ".join(errors))


def collect_prices(symbols: list[str], resolution: str = COLLECT_RESOLUTION, start: date | None = None,
                   end: date | None = None, pause: float = REQUEST_PAUSE, source: str = "auto",
                   indexes: list[str] = DEFAULT_INDEXES) -> pd.DataFrame:
    end = end or datetime.now(VN_TZ).date()
    start = start or end - timedelta(days=180 if resolution in ("1D", "1W") else WARMUP_DAYS)
    names = list(dict.fromkeys(validate_symbol(s) for s in [*symbols, *indexes]))
    frames, failed = [], []
    for i, sym in enumerate(names):
        try:
            df = fetch_prices(sym, start, end, resolution, source=source)
            if df.empty:
                log.info("%s: chưa có nến %s đã đóng.", sym, resolution)
            else:
                frames.append(df)
        except (RuntimeError, ValueError) as err:
            failed.append(sym)
            log.warning("%s: không lấy được giá: %s", sym, err)
        if pause > 0 and i < len(names) - 1:
            _rate_limit_sleep(max(REQUEST_PAUSE, float(pause)))
    log.info("Giá: %d/%d mã có dữ liệu.", len(frames), len(names))
    if failed:
        log.warning("Thiếu giá của: %s", ", ".join(failed))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=PRICE_COLUMNS)


# ---- Thu thập định kỳ đúng mốc nến đóng ----
def next_collection_time(now: datetime, resolution: str = COLLECT_RESOLUTION, all_hours: bool = False) -> datetime:
    step = BAR_SECONDS[resolution]
    base = (int(now.timestamp()) // step) * step
    for k in range(14 * 86400 // step + 2):
        bar_close = datetime.fromtimestamp(base + k * step, VN_TZ)
        due = bar_close + timedelta(seconds=CANDLE_CLOSE_DELAY)
        minutes = bar_close.hour * 60 + bar_close.minute
        in_session = bar_close.weekday() < 5 and (9 * 60 < minutes <= 11 * 60 + 30 or 13 * 60 < minutes <= 15 * 60)
        if due > now and (all_hours or in_session):
            return due
    return now + timedelta(seconds=step)


def run_periodic(symbols: list[str], resolution: str = COLLECT_RESOLUTION, csv_path: str = "output/prices_15.csv",
                 iterations: int | None = None, all_hours: bool = False) -> None:
    n = 0
    try:
        while iterations is None or n < iterations:
            n += 1
            try:
                df = collect_prices(symbols, resolution)
                total = save_csv(df, csv_path, ["symbol", "resolution", "time"])
                log.info("Lần %d: nhận %d nến, CSV có %d dòng.", n, len(df), total)
            except Exception:  # noqa: BLE001
                log.exception("Chu kỳ #%d lỗi, thử lại ở chu kỳ sau.", n)
            if iterations is not None and n >= iterations:
                break
            due = next_collection_time(datetime.now(VN_TZ), resolution, all_hours)
            log.info("Lần thu thập tiếp theo: %s", due.strftime("%Y-%m-%d %H:%M:%S"))
            while (left := (due - datetime.now(VN_TZ)).total_seconds()) > 0:
                time.sleep(min(left, 30))
    except KeyboardInterrupt:
        log.info("Đã dừng theo yêu cầu.")


# ==============================================================================
# CHỈ SỐ TÀI CHÍNH: vnstock (chính) + CSV (dự phòng)
# ==============================================================================
def _blank() -> pd.DataFrame:
    return pd.DataFrame({"period": pd.Series(dtype=str), **{m: pd.Series(dtype=float) for m in METRICS}})


def _std_period(label, report_type: str) -> str | None:
    text = str(label).upper()
    m = re.search(r"(?:19|20)\d{2}", text)
    if not m:
        return None
    quarter = re.search(r"[1-4]", text[:m.start()] + " " + text[m.end():])
    return f"{m.group(0)}-Q{quarter.group(0)}" if report_type == "quarter" and quarter else m.group(0)


def _period_key(period: str) -> tuple[int, int]:
    m = re.match(r"(\d{4})(?:-Q(\d))?", str(period))
    return (int(m.group(1)), int(m.group(2) or 0)) if m else (-1, -1)


def _default_anchor(report_type: str) -> str:
    today = datetime.now(VN_TZ).date()
    if report_type == "year":
        return str(today.year - 1)
    q = (today.month - 1) // 3 + 1
    return f"{today.year - 1}-Q4" if q == 1 else f"{today.year}-Q{q - 1}"


def _expected_periods(latest: str, n: int, report_type: str) -> list[str]:
    y, q = _period_key(latest)
    out = []
    for _ in range(n):
        if report_type == "quarter":
            out.append(f"{y}-Q{q}")
            y, q = (y - 1, 4) if q == 1 else (y, q - 1)
        else:
            out.append(str(y))
            y -= 1
    return out


def _keys_of(label) -> set[str]:
    if isinstance(label, tuple):
        parts = [_norm(p) for p in label if str(p).strip() and not str(p).startswith("Unnamed")]
        return set(parts) | {"".join(parts)}
    return {_norm(label)}


def _find_column(df: pd.DataFrame, aliases):
    for alias in aliases:
        norm_alias = _norm(alias)
        for col in df.columns:
            keys = _keys_of(col)
            if norm_alias in keys:
                return col
    return None


def _coerce_csv_metric(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    mapped = {}
    for key, aliases in METRIC_ALIASES.items():
        col = _find_column(df, aliases)
        if col is not None:
            mapped[key] = df[col]
    for key, s in mapped.items():
        df[key] = pd.to_numeric(s, errors='coerce')
    if 'pe' in df.columns and 'pe' not in mapped:
        df['pe'] = pd.to_numeric(df.get('pe', pd.Series(dtype=float)), errors='coerce')
    if 'after_tax_margin' in df.columns and 'net_margin' not in df.columns:
        df['net_margin'] = pd.to_numeric(df['after_tax_margin'], errors='coerce')
    if 'debt_to_equity' in df.columns and 'debt_to_equity' not in df.columns:
        df['debt_to_equity'] = pd.to_numeric(df['debt_to_equity'], errors='coerce')
    if 'profit_after_tax' not in df.columns and 'market_cap' in df.columns and 'pe' in df.columns:
        market_cap = pd.to_numeric(df['market_cap'], errors='coerce')
        pe = pd.to_numeric(df['pe'], errors='coerce')
        df['profit_after_tax'] = market_cap / pe.replace(0, pd.NA)
    return df


def _col(df: pd.DataFrame, col) -> pd.Series:
    s = df[col]
    return s.iloc[:, 0] if isinstance(s, pd.DataFrame) else s


def _from_period_rows(df: pd.DataFrame, report_type: str) -> pd.DataFrame:
    out = pd.DataFrame(index=range(len(df)))
    for metric, aliases in METRIC_ALIASES.items():
        col = _find_column(df, aliases)
        out[metric] = float("nan") if col is None else pd.to_numeric(_col(df, col), errors="coerce").to_numpy()
    y, q, p = (_find_column(df, k) for k in (YEAR_KEYS, QUARTER_KEYS, PERIOD_KEYS))
    by_period = [_std_period(v, report_type) for v in _col(df, p)] if p is not None else None
    if by_period and any(v and (report_type == "year" or "-Q" in v) for v in by_period):
        out["period"] = by_period
    elif y is not None:
        years = pd.to_numeric(_col(df, y), errors="coerce").to_numpy()
        quarters = pd.to_numeric(_col(df, q), errors="coerce").to_numpy() if q is not None else [float("nan")] * len(df)
        out["period"] = [None if pd.isna(yr) else
                         (f"{int(yr)}-Q{int(qt)}" if report_type == "quarter" and 1 <= (qt if pd.notna(qt) else 0) <= 4 else str(int(yr)))
                         for yr, qt in zip(years, quarters)]
    else:
        out["period"] = by_period
    return out


def _from_item_rows(df: pd.DataFrame, report_type: str) -> pd.DataFrame:
    flat = df.copy()
    flat.columns = [str(c) for c in flat.columns]
    meta = [i for i, c in enumerate(flat.columns) if _norm(c) in {"item", "itemen", "itemid"}]
    cols, seen = [], set()
    for i, c in enumerate(flat.columns):
        if i not in meta and c not in seen:
            seen.add(c)
            cols.append(i)
    row_keys = [{_norm(flat.iat[r, m]) for m in meta if pd.notna(flat.iat[r, m])} for r in range(len(flat))]
    out = pd.DataFrame({"period": [_std_period(flat.columns[i], report_type) for i in cols]})
    for metric, aliases in METRIC_ALIASES.items():
        out[metric] = float("nan")
        for alias in aliases:
            norm_alias = _norm(alias)
            r = next((r for r, k in enumerate(row_keys) if any(norm_alias in item or item in norm_alias for item in k)), None)
            if r is not None:
                out[metric] = pd.to_numeric(flat.iloc[r, cols], errors="coerce").to_numpy()
                break
    return out


def to_tidy(df: pd.DataFrame | None, report_type: str) -> pd.DataFrame:
    if df is None or df.empty:
        return _blank()
    is_items = any(_norm(c) in {"itemid", "itemen"} for c in df.columns)
    tidy = _from_item_rows(df, report_type) if is_items else _from_period_rows(df, report_type)
    tidy = tidy.dropna(subset=["period"])
    tidy = tidy.groupby("period", as_index=False).first()
    return tidy[["period", *METRICS]]


_last_call = [0.0]


def _finance_client(symbol: str, period: str):
    for module_name, classes in (("vnfinancial", ("Financial", "VnFinancial", "Finance", "Client")),
                                 ("vnstock.api.financial", ("Finance",))):
        try:
            module = __import__(module_name, fromlist=list(classes))
        except Exception:
            continue
        for name in classes:
            cls = getattr(module, name, None)
            if cls is None:
                continue
            try:
                return cls(source="VCI", symbol=symbol, period=period)
            except Exception:
                continue
    from vnstock import Vnstock
    return Vnstock().stock(symbol=symbol, source="VCI").finance


def _call(method, report_type: str):
    for attempt in range(1, VNSTOCK_RETRIES + 1):
        _rate_limit_sleep(VNSTOCK_CALL_INTERVAL)
        wait = VNSTOCK_CALL_INTERVAL - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()
        try:
            for extra in ({"get_all": True}, {}):
                try:
                    return method(period=report_type, lang="en", **extra)
                except TypeError:
                    continue
            raise TypeError("Không gọi được phương thức tài chính của vnstock")
        except BaseException as err:
            limited = isinstance(err, SystemExit) or re.search(r"rate|limit|429|too many", str(err), re.I)
            if not limited or attempt == VNSTOCK_RETRIES:
                raise RuntimeError(f"Lỗi gọi vnstock: {err}") from err
            sleep_time = 30 * attempt
            log.warning("vnstock bị giới hạn tốc độ (Rate Limit), tạm dừng %ds rồi thử lại...", sleep_time)
            time.sleep(sleep_time)


def _usable(tidy: pd.DataFrame, report_type: str) -> bool:
    good = tidy["period"].astype(str).str.contains("-Q") if report_type == "quarter" else tidy["period"].notna()
    return bool((good & tidy[METRICS].notna().any(axis=1)).any())


def fetch_financial_vnstock(symbol: str, report_type: str) -> tuple[pd.DataFrame, str]:
    tidy, notes = _blank(), []
    try:
        client = _finance_client(symbol, report_type)
        raw = _call(client.ratio, report_type)
        tidy = to_tidy(raw, report_type)
    except BaseException as err:
        return tidy, f"{type(err).__name__}: {err}"
    if not _usable(tidy, report_type):
        notes.append("ratio không dùng được")
        tidy = _blank()
    if tidy.empty or tidy[["eps", "profit_after_tax"]].isna().any(axis=None):
        try:
            raw_inc = _call(client.income_statement, report_type)
            inc = to_tidy(raw_inc, report_type)[["period", "eps", "profit_after_tax"]]
            tidy = tidy.set_index("period").combine_first(inc.set_index("period")).reset_index()[["period", *METRICS]]
        except BaseException as err:
            notes.append(f"income_statement: {err}")
    return tidy, " || ".join(notes)


# ---- Nguồn 2: CSV dự phòng ----
_warned_paths: set[str] = set()


def _resolve_csv(path: str | None) -> str:
    path = path or FINANCIAL_CSV_PATH
    if os.path.isabs(path) or os.path.exists(path):
        return path
    alt = os.path.join(os.path.dirname(os.path.abspath(__file__)), path)
    return alt if os.path.exists(alt) else path


def _read_financial_csv(path: str) -> pd.DataFrame:
    for sep in (None, ",", ";"):
        try:
            df = pd.read_csv(path, engine="python") if sep is None else pd.read_csv(path, sep=sep, engine="python")
            if not df.empty and isinstance(df.columns, pd.Index):
                return df
        except Exception:
            continue
    return pd.DataFrame()


def load_csv_backup(symbol: str, report_type: str, path: str | None = None) -> pd.DataFrame:
    path = _resolve_csv(path)
    if not os.path.exists(path):
        if path not in _warned_paths:
            _warned_paths.add(path)
            log.warning("KHÔNG TÌM THẤY CSV dự phòng: %s", os.path.abspath(path))
        return _blank()
    try:
        df = _read_financial_csv(path)
        if df.empty:
            raise ValueError("CSV rỗng")
        df = _coerce_csv_metric(df)
        t = _find_column(df, TICKER_KEYS)
        if t is None:
            raise ValueError("CSV thiếu cột ticker/symbol")
        df = df[df[t].astype(str).str.strip().str.upper() == symbol]
        q = _find_column(df, QUARTER_KEYS)
        if report_type == "year" and q is not None:
            df = df[pd.to_numeric(_col(df, q), errors="coerce") == 4]
        return to_tidy(df.reset_index(drop=True), report_type)
    except Exception as err:
        log.warning("%s: đọc CSV dự phòng lỗi: %s", symbol, err)
        return _blank()


# ---- Gộp + chuẩn hóa ----
def get_financials(symbol: str, report_type: str = "quarter", max_periods: int = MAX_FINANCE_PERIODS,
                   csv_path: str | None = None) -> pd.DataFrame:
    symbol = validate_symbol(symbol)
    if report_type not in ("quarter", "year"):
        raise ValueError("report_type phải là 'quarter' hoặc 'year'")

    vn, vn_err = fetch_financial_vnstock(symbol, report_type)
    vn = vn.set_index("period")
    bk = load_csv_backup(symbol, report_type, csv_path).set_index("period")

    merged = vn.combine_first(bk)[METRICS]
    if report_type == "quarter":
        merged = merged[merged.index.astype(str).str.contains("-Q")]
    latest = max([_default_anchor(report_type), *merged.index], key=_period_key)
    merged = merged.reindex(_expected_periods(latest, max_periods, report_type))
    merged.index.name = "period"

    has = merged[METRICS].notna().any(axis=1)
    vn_part = vn.reindex(merged.index)[METRICS]
    filled = (vn_part.isna() & merged.notna()).any(axis=1)
    merged["source"] = ["none" if not h else "csv" if not v else "vnstock+csv" if f else "vnstock"
                        for h, v, f in zip(has, vn_part.notna().any(axis=1), filled)]
    merged["status"] = ["MISSING" if not h else "OK" if a else "PARTIAL"
                        for h, a in zip(has, merged[METRICS].notna().all(axis=1))]

    merged = merged.reset_index()
    for m, digits in METRIC_DIGITS.items():
        merged[m] = merged[m].astype(float).round(digits)
    merged["symbol"], merged["period_type"] = symbol, report_type
    return merged[FIN_COLUMNS]


def get_financials_many(symbols: list[str], report_type: str = "quarter", max_periods: int = MAX_FINANCE_PERIODS,
                        csv_path: str | None = None, pause: float = REQUEST_PAUSE) -> pd.DataFrame:
    frames = []
    for i, sym in enumerate(symbols):
        try:
            frames.append(get_financials(sym, report_type, max_periods, csv_path))
        except BaseException as err:
            log.warning("%s: %s", sym, err)
        if pause > 0 and i < len(symbols) - 1:
            _rate_limit_sleep(max(REQUEST_PAUSE, float(pause)))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=FIN_COLUMNS)


# ==============================================================================
# KIỂM TRA CHẤT LƯỢNG DỮ LIỆU
# ==============================================================================
def check_data(prices: pd.DataFrame | None = None, fin: pd.DataFrame | None = None,
               expected: list[str] | None = None, since_quarter: str = "2024-Q1") -> None:
    if prices is not None:
        print("=== GIÁ ===")
        if prices.empty:
            print("Không có dòng giá nào.")
        else:
            g = prices.groupby("symbol").agg(so_nen=("time", "size"), tu=("time", "min"), den=("time", "max"), nguon=("source", "first"))
            print(g.to_string())
            print(f"Dòng trùng (symbol, resolution, time): {int(prices.duplicated(['symbol', 'resolution', 'time']).sum())}"
                  f" | nguồn lạ/giả: {sorted(set(prices['source']) - KNOWN_SOURCES) or 'không'}")
        if expected:
            absent = [s for s in expected if s not in set(prices.get("symbol", []))]
            print("Mã CHƯA có giá:", absent or "không")
    if fin is not None:
        print("=== BCTC ===")
        if fin.empty:
            print("Không có dòng BCTC nào.")
        else:
            rows = []
            for (sym, kind), g in fin.groupby(["symbol", "period_type"], sort=False):
                st = g["status"].value_counts()
                scope = g[g["period"].map(_period_key) >= _period_key(since_quarter)] if kind == "quarter" else g
                rows.append({"symbol": sym, "type": kind, "OK": st.get("OK", 0), "PARTIAL": st.get("PARTIAL", 0),
                             "MISSING": st.get("MISSING", 0), "sources": "/".join(sorted(set(g["source"]) - {"none"})) or "-",
                             "missing_periods": ",".join(scope.loc[scope["status"] == "MISSING", "period"]) or "-"})
            print(pd.DataFrame(rows).fillna("").to_string(index=False))
            print(f"Dòng trùng (symbol, period_type, period): {int(fin.duplicated(['symbol', 'period_type', 'period']).sum())}"
                  f" | nguồn lạ/giả: {sorted(set(fin['source']) - KNOWN_SOURCES) or 'không'}")
        if expected:
            absent = [s for s in expected if s not in set(fin.get("symbol", []))]
            print("Mã CHƯA có trong BCTC:", absent or "không")