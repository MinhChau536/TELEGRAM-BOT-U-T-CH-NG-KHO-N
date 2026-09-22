# TELEGRAM BOT CHỨNG KHOÁN
Repository này hiện gồm hai phần Python độc lập:

- `DNSE.py`: lấy, làm sạch và chuẩn hóa dữ liệu giá cùng chỉ số tài chính.
- `layer2.py`: đọc dữ liệu giá Daily từ SQLite, tính Relative Strength 6 tháng và phân loại market regime của VN-Index.

Đây **chưa phải Telegram Bot hoàn chỉnh**. Repository hiện chưa có `main.py`, package `bot/`, package `strategy/`, scheduler hoặc chức năng gửi tin nhắn Telegram.

## 1. Cấu trúc repository

```text
.
├── DNSE.py          # Data pipeline: giá và chỉ số tài chính
├── layer2.py        # RS 6 tháng và Market Regime
├── Filedata.csv     # CSV dự phòng cho dữ liệu tài chính
├── prices_1D.csv    # Dữ liệu giá Daily mẫu
├── README.md
└── .gitignore
```

Thư mục có tên bắt đầu bằng `Code xử lí data đầu vào -...` là bản export/snapshot cục bộ và đã được đưa vào `.gitignore`. Không dùng thư mục này làm source chính.

## 2. Yêu cầu môi trường

- Python 3.10 trở lên
- Kết nối Internet khi gọi DNSE hoặc vnstock
- Các thư viện:

```powershell
python -m pip install pandas requests numpy vnstock
```

Nên dùng môi trường ảo trên Windows:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install pandas requests numpy vnstock
```

## 3. Cấu hình API và đường dẫn

`DNSE.py` đọc các biến môi trường sau:

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `DNSE_API_KEY` | Rỗng | API key DNSE, không bắt buộc nếu dùng dữ liệu công khai |
| `DNSE_SECRET_KEY` | Rỗng | Secret key DNSE |
| `FINANCIAL_CSV_PATH` | `Filedata.csv` ở root | CSV dự phòng cho dữ liệu tài chính |

PowerShell:

```powershell
$env:DNSE_API_KEY = "your_api_key"
$env:DNSE_SECRET_KEY = "your_secret_key"
$env:FINANCIAL_CSV_PATH = "$PWD\Filedata.csv"
```

Không ghi API key hoặc secret trực tiếp vào source code. Nếu key từng xuất hiện trong file export, hãy thu hồi key cũ và tạo key mới.

## 4. Chạy pipeline dữ liệu

Chạy ví dụ tích hợp ở cuối `DNSE.py`:

```powershell
python .\DNSE.py
```

Luồng xử lý hiện tại:

1. Lấy dữ liệu giá 15 phút cho các mã mẫu và tự thêm `VNINDEX`.
2. Thử nguồn DNSE trước, sau đó fallback sang vnstock.
3. Loại nến chưa đóng, chuẩn hóa múi giờ Việt Nam và các cột OHLCV.
4. Lấy báo cáo tài chính theo quý và theo năm từ vnstock.
5. Bù dữ liệu thiếu từ `Filedata.csv`.
6. Kiểm tra mã thiếu, dòng trùng, nguồn dữ liệu lạ và kỳ báo cáo thiếu.
7. Ghi kết quả vào:

```text
output/prices_15.csv
output/financials.csv
```

Các file trong `output/` là dữ liệu sinh ra, không phải source code. `.gitignore` đã loại thư mục này khỏi Git.

### Sử dụng module trong Python

```python
from datetime import date
import DNSE

prices = DNSE.collect_prices(
    ["FPT", "VNM", "HPG"],
    resolution="1D",
    start=date(2025, 1, 1),
    end=date(2025, 12, 31),
)

financials = DNSE.get_financials(
    "FPT",
    report_type="quarter",
    max_periods=10,
)
```

### Các định dạng chuẩn

Giá được chuẩn hóa thành các cột:

```text
symbol, resolution, time, open, high, low, close, volume, source
```

Dữ liệu tài chính được chuẩn hóa thành:

```text
symbol, period_type, period, roe, roa, eps, pe, pb,
net_margin, debt_to_equity, profit_after_tax, status, source
```

`status` có thể là `OK`, `PARTIAL` hoặc `MISSING`. `source` cho biết dữ liệu đến từ `vnstock`, `csv` hoặc kết hợp cả hai.

## 5. Kiểm tra chất lượng dữ liệu

`DNSE.check_data()` dùng để kiểm tra dòng trùng, mã thiếu, nguồn lạ và kỳ báo cáo thiếu:

```python
import pandas as pd
import DNSE

prices = pd.read_csv("prices_1D.csv")
financials = pd.read_csv("output/financials.csv")

DNSE.check_data(
    prices=prices,
    fin=financials,
    expected=["FPT", "VNM", "HPG"],
)
```

Để chẩn đoán vì sao thiếu chỉ số tài chính:

```python
DNSE.diagnose_financials("FPT", report_type="quarter")
```

## 6. Layer 2: Relative Strength và Market Regime

`layer2.py` thực hiện:

- Đọc các dòng `resolution = '1D'` từ bảng `prices` trong SQLite.
- Tính lợi nhuận 6 tháng bằng độ trễ 126 phiên.
- Xếp hạng `rs_percentile` theo từng ngày.
- Giữ phiên mới nhất của từng mã.
- Tính SMA200 của VN-Index bằng vnstock.
- Đánh dấu `Bullish` khi VN-Index > SMA200.
- Đánh dấu `layer2_pass` khi `rs_percentile >= 70` và regime là `Bullish`.

Chạy:

```powershell
python .\layer2.py
```

### Lưu ý quan trọng về dữ liệu Layer 2

`layer2.py` hiện chưa đọc file `prices_1D.csv`. Nó đang dùng đường dẫn SQLite cố định trong mã:

```text
C:\Users\HP\Tang2_3_Strategy\output\market_data.db
```

Vì vậy lệnh trên chỉ chạy được khi database tồn tại đúng đường dẫn đó và có bảng `prices` với tối thiểu các cột:

```text
symbol, resolution, time, close
```

`prices_1D.csv` trong repository hiện là dữ liệu mẫu cho pipeline, chưa được nối tự động vào `layer2.py`.

## 7. Phạm vi chưa có

Các nội dung sau mới là kế hoạch, chưa được triển khai trong code hiện tại:

- Telegram Bot và các lệnh `/start`, `/signals`, `/check`.
- Tầng Quality, Momentum và ATR Risk hoàn chỉnh.
- Scheduler chạy định kỳ.
- Backtest engine.
- `requirements.txt`.
- Test tự động.
- Database cache trong repository.

Không nên dùng các lệnh hoặc thư mục trên như thể chúng đã tồn tại.

## 8. Hướng phát triển tiếp theo

Thứ tự nên làm:

1. Đưa `DB_PATH` của `layer2.py` thành biến môi trường hoặc tham số hàm.
2. Cho `layer2.py` đọc được `prices_1D.csv`, hoặc thống nhất một nguồn SQLite duy nhất.
3. Tạo `requirements.txt`.
4. Bổ sung test cho chuẩn hóa OHLCV, đọc CSV tài chính và tính RS.
5. Sau khi pipeline dữ liệu ổn định, mới thêm các tầng chiến lược và Telegram Bot.

## 9. Miễn trừ trách nhiệm

Dự án phục vụ mục đích học tập và nghiên cứu. Dữ liệu hoặc kết quả lọc không phải là khuyến nghị đầu tư và không đảm bảo lợi nhuận.
