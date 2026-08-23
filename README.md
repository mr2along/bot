# Vietlott Power 6/55 Scraper Bot

Bot dòng lệnh dùng Python để lấy dữ liệu kết quả xổ số Vietlott Power 6/55 từ trang ketquadientoan.com, xuất ra JSON/CSV và lưu thành dataset cục bộ.

## Cài đặt

Yêu cầu Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Sử dụng

Lấy toàn bộ kỳ quay trong khoảng ngày mặc định từ URL yêu cầu và lưu vào dataset:

```bash
python vietlott_bot.py --dataset-dir dataset/power655
```

Xuất riêng ra CSV:

```bash
python vietlott_bot.py --format csv --output power655.csv
```

Tùy chỉnh khoảng ngày:

```bash
python vietlott_bot.py --datef 20-07-2016 --datet 14-09-2027 --format json --output power655.json
```

In kết quả ra màn hình:

```bash
python vietlott_bot.py --format json
```

Khi dùng `--dataset-dir`, bot tạo/ghi đè 3 file trong thư mục dataset:

- `power655.jsonl`: mỗi dòng là một bản ghi JSON.
- `power655.csv`: bảng dữ liệu để mở bằng Excel/BI tools.
- `metadata.json`: thông tin nguồn, số bản ghi và thời điểm cập nhật.

## Trường dữ liệu

Mỗi bản ghi gồm:

- `draw_id`: mã/kỳ quay nếu trang nguồn có hiển thị.
- `draw_date`: ngày quay dạng `DD-MM-YYYY`.
- `numbers`: 6 số chính.
- `special_number`: số đặc biệt nếu trang nguồn có hiển thị.
- `raw_text`: nội dung thô của dòng kết quả để tiện kiểm tra khi cấu trúc trang thay đổi.
