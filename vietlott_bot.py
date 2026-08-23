#!/usr/bin/env python3
"""Scrape Vietlott Power 6/55 results from ketquadientoan.com."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from typing import Iterable, Sequence
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DEFAULT_DATE_FROM = "20-07-2016"
DEFAULT_DATE_TO = "14-09-2027"
BASE_URL = "https://www.ketquadientoan.com/tat-ca-ky-xo-so-power-655.html"
DATE_RE = re.compile(r"\b\d{2}-\d{2}-\d{4}\b")
NUMBER_RE = re.compile(r"\b\d{1,2}\b")
DRAW_RE = re.compile(r"(?:kỳ|ky|#)\s*([0-9]{3,6})", re.IGNORECASE)
ROW_TAGS = {"tr", "li"}
BLOCK_TAGS = {"div", "p", "section", "article"}


@dataclass(slots=True)
class Power655Result:
    draw_id: str | None
    draw_date: str
    numbers: list[str]
    special_number: str | None
    raw_text: str


class TextCandidateParser(HTMLParser):
    """Collect visible text snippets that are likely to contain draw results."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._stack: list[tuple[str, list[str]]] = []
        self.candidates: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001 - HTMLParser API
        if tag in ROW_TAGS | BLOCK_TAGS:
            self._stack.append((tag, []))

    def handle_endtag(self, tag: str) -> None:
        if not self._stack:
            return
        current_tag, parts = self._stack[-1]
        if current_tag != tag:
            return
        self._stack.pop()
        text = " ".join(" ".join(parts).split())
        if text:
            self.candidates.append(text)
            if self._stack:
                self._stack[-1][1].append(text)

    def handle_data(self, data: str) -> None:
        if self._stack:
            self._stack[-1][1].append(data)


def build_url(date_from: str, date_to: str) -> str:
    return f"{BASE_URL}?{urlencode({'datef': date_from, 'datet': date_to})}"


def fetch_html(url: str, timeout: int = 30) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
        ),
        "Accept-Language": "vi,en;q=0.9",
    }
    request = Request(url, headers=headers)
    with urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def text_candidates(html: str) -> list[str]:
    parser = TextCandidateParser()
    parser.feed(html)
    parser.close()
    return parser.candidates


def parse_results(html: str) -> list[Power655Result]:
    results: list[Power655Result] = []
    seen: set[tuple[str, tuple[str, ...], str | None]] = set()

    for text in text_candidates(html):
        date_match = DATE_RE.search(text)
        if not date_match:
            continue

        draw_match = DRAW_RE.search(text)
        ignored_spans = [date_match.span()]
        if draw_match:
            ignored_spans.append(draw_match.span(1))

        balls: list[str] = []
        for number_match in NUMBER_RE.finditer(text):
            if any(start <= number_match.start() < end for start, end in ignored_spans):
                continue
            value = int(number_match.group(0))
            if 1 <= value <= 55:
                balls.append(number_match.group(0).zfill(2))

        if len(balls) < 6:
            continue

        item = Power655Result(
            draw_id=draw_match.group(1) if draw_match else None,
            draw_date=date_match.group(0),
            numbers=balls[:6],
            special_number=balls[6] if len(balls) > 6 else None,
            raw_text=text,
        )
        key = (item.draw_date, tuple(item.numbers), item.special_number)
        if key in seen:
            continue
        seen.add(key)
        results.append(item)

    return results


def write_json(results: Iterable[Power655Result], output: str | None) -> None:
    payload = json.dumps([asdict(result) for result in results], ensure_ascii=False, indent=2)
    if output:
        with open(output, "w", encoding="utf-8") as file:
            file.write(payload + "\n")
    else:
        print(payload)


def csv_row(result: Power655Result) -> dict[str, str | None]:
    row = asdict(result)
    row["numbers"] = "-".join(result.numbers)
    return row


def write_csv(results: Iterable[Power655Result], output: str | None) -> None:
    fieldnames = ["draw_id", "draw_date", "numbers", "special_number", "raw_text"]
    target = open(output, "w", newline="", encoding="utf-8") if output else sys.stdout
    try:
        writer = csv.DictWriter(target, fieldnames=fieldnames)
        writer.writeheader()
        for result in results:
            writer.writerow(csv_row(result))
    finally:
        if output:
            target.close()


def upload_dataset(results: Sequence[Power655Result], dataset_dir: str, source_url: str) -> None:
    """Persist scraped results into a local dataset directory.

    The directory is intentionally simple so it can be committed, synced, or
    uploaded by another job to storage services such as Hugging Face Datasets,
    S3, or Google Cloud Storage.
    """
    dataset_path = Path(dataset_dir)
    dataset_path.mkdir(parents=True, exist_ok=True)

    jsonl_path = dataset_path / "power655.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as file:
        for result in results:
            file.write(json.dumps(asdict(result), ensure_ascii=False) + "\n")

    csv_path = dataset_path / "power655.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=["draw_id", "draw_date", "numbers", "special_number", "raw_text"],
        )
        writer.writeheader()
        for result in results:
            writer.writerow(csv_row(result))

    metadata = {
        "name": "vietlott_power_655",
        "source_url": source_url,
        "record_count": len(results),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "files": [jsonl_path.name, csv_path.name],
    }
    metadata_path = dataset_path / "metadata.json"
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Lấy kết quả Vietlott Power 6/55.")
    parser.add_argument("--datef", default=DEFAULT_DATE_FROM, help="Ngày bắt đầu, dạng DD-MM-YYYY.")
    parser.add_argument("--datet", default=DEFAULT_DATE_TO, help="Ngày kết thúc, dạng DD-MM-YYYY.")
    parser.add_argument("--url", help="URL nguồn tuỳ chỉnh; nếu bỏ trống sẽ dựng từ datef/datet.")
    parser.add_argument("--format", choices=("json", "csv"), default="json", help="Định dạng xuất dữ liệu.")
    parser.add_argument("--output", help="Đường dẫn file đầu ra; nếu bỏ trống sẽ in ra stdout.")
    parser.add_argument(
        "--dataset-dir",
        help="Thư mục dataset để lưu power655.jsonl, power655.csv và metadata.json.",
    )
    args = parser.parse_args()

    url = args.url or build_url(args.datef, args.datet)
    html = fetch_html(url)
    results = parse_results(html)
    if args.dataset_dir:
        upload_dataset(results, args.dataset_dir, url)
    if args.format == "csv":
        write_csv(results, args.output)
    else:
        write_json(results, args.output)
    print(f"Fetched {len(results)} results from {url}", file=sys.stderr)
    if args.dataset_dir:
        print(f"Uploaded dataset to {args.dataset_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
