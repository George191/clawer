import argparse
import os
import re
import sys
import time


CHUNK_SIZE = 8 * 1024 * 1024
PUB_LINE_RE = re.compile(rb"^(US-[A-Z0-9\-]+),")


def parse_args():
    parser = argparse.ArgumentParser(
        description="从按年份划分的专利 CSV 文件中抽取 publication number（每行首列），"
                    "并输出到指定文本文件。"
    )
    parser.add_argument(
        "--data-dir",
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data"),
        help="CSV 文件所在目录，默认 ../data",
    )
    parser.add_argument(
        "--years",
        default="2010,2011,2012,2013,2014",
        help="要处理的年份列表，使用逗号分隔。默认: 2010,2011,2012,2013,2014",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="输出文件路径；若省略，则按 <起始年份>-<结束年份> 自动生成文件名。",
    )
    parser.add_argument(
        "--report-interval",
        type=float,
        default=30.0,
        help="进度日志的间隔（秒）。默认 30 秒。",
    )
    return parser.parse_args()


def log(log_f, msg):
    log_f.write(msg + "\n")
    log_f.flush()
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def extract_from_csv(csv_path, pub_re, seen, results, log_f, tag, report_interval):
    file_start = time.time()
    total_file = os.path.getsize(csv_path)
    file_bytes = 0
    matched = 0
    new_unique = 0
    last_report = time.time()
    skip_first_line = True

    with open(csv_path, "rb", buffering=CHUNK_SIZE) as f:
        for raw_line in f:
            line_len = len(raw_line)
            file_bytes += line_len
            if skip_first_line:
                skip_first_line = False
                continue
            m = pub_re.match(raw_line)
            if m:
                matched += 1
                pub = m.group(1)
                if pub not in seen:
                    seen.add(pub)
                    results.append(pub)
                    new_unique += 1
            now = time.time()
            if now - last_report >= report_interval:
                pct = (file_bytes / total_file * 100.0) if total_file else 0.0
                elapsed = now - file_start
                mb_s = (file_bytes / 1024 / 1024) / elapsed if elapsed > 0 else 0.0
                log(
                    log_f,
                    f"[{tag}] progress {file_bytes}/{total_file} bytes "
                    f"({pct:.2f}%) rate={mb_s:.1f} MB/s "
                    f"matched_so_far={matched} new_unique_so_far={new_unique} "
                    f"total_unique={len(results)}",
                )
                last_report = now

    elapsed = time.time() - file_start
    log(
        log_f,
        f"[{tag}] DONE bytes_processed={file_bytes} matched={matched} "
        f"new_unique={new_unique} total_unique={len(results)} elapsed={elapsed:.1f}s",
    )
    return matched, new_unique


def main():
    args = parse_args()
    data_dir = os.path.abspath(args.data_dir)
    years = sorted({int(y.strip()) for y in args.years.split(",") if y.strip()})
    if not years:
        print("ERROR: 未指定有效年份", file=sys.stderr)
        sys.exit(2)

    if args.output:
        output_path = os.path.abspath(args.output)
    else:
        output_path = os.path.join(
            data_dir, f"publication_numbers_{years[0]}-{years[-1]}.txt"
        )

    start = time.time()
    log_path = output_path + ".log"
    log_f = open(log_path, "w", encoding="utf-8")

    log(log_f, f"START data_dir={data_dir} output={output_path} years={years}")

    missing = []
    for year in years:
        csv_path = os.path.join(data_dir, f"{year}.csv")
        if not os.path.exists(csv_path):
            missing.append(csv_path)
    if missing:
        for p in missing:
            log(log_f, f"[WARN] 文件不存在: {p}")
        print("ERROR: 部分年份 CSV 文件缺失，已退出", file=sys.stderr)
        sys.exit(1)

    seen = set()
    results = []
    for year in years:
        csv_path = os.path.join(data_dir, f"{year}.csv")
        log(log_f, f"[{year}] 开始处理 {csv_path}")
        extract_from_csv(
            csv_path, PUB_LINE_RE, seen, results, log_f, tag=str(year),
            report_interval=args.report_interval,
        )

    log(log_f, f"写入输出文件: {output_path}")
    with open(output_path, "wb") as f:
        for pub in results:
            f.write(pub)
            f.write(b"\n")

    log(log_f, f"WROTE {len(results)} lines to {output_path}")
    log(log_f, f"TOTAL elapsed={time.time() - start:.1f}s")
    log_f.close()
    print(f"\n[DONE] {output_path} 共 {len(results)} 条")


if __name__ == "__main__":
    main()
