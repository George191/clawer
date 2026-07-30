"""Filter exported Google Patent publication numbers against MongoDB.

The source TXT files are never modified. Publication numbers whose generated
``_meta.record_id`` is absent from MongoDB are written to a separate directory.
Progress is checkpointed after each MongoDB batch for safe resume.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import msvcrt
import os
import re
import time
from collections.abc import Iterable
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO

from pymongo import MongoClient
from pymongo.collection import Collection

from app.config.settings import settings

DEFAULT_INPUT_DIR = Path(
    r"D:\BaiduNetdiskDownload\Google Patent\publication_number_txt"
)
DEFAULT_OUTPUT_DIR = Path(
    r"D:\BaiduNetdiskDownload\Google Patent\publication_numbers_not_in_mongo"
)
DEFAULT_COLLECTION = "google_patent"
DEFAULT_BATCH_SIZE = 5_000
DEFAULT_CHUNK_SIZE = 3_000_000
SOURCE_PATTERN = "publication_numbers_*.txt"
STATE_FILENAME = "filter_state.json"
INVALID_FILENAME = "invalid_publication_numbers.txt"
LOCK_FILENAME = "filter.lock"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Write publication numbers that do not exist in MongoDB."
    )
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--collection", default=DEFAULT_COLLECTION)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    return parser.parse_args()


def discover_source_files(input_dir: Path) -> list[Path]:
    return sorted(path for path in input_dir.glob(SOURCE_PATTERN) if path.is_file())


def source_manifest(source_files: Iterable[Path]) -> list[dict[str, Any]]:
    return [
        {"name": path.name, "size": path.stat().st_size}
        for path in source_files
    ]


def normalize_publication_number(publication_number: str) -> str:
    return re.sub(r"[-\s]", "", publication_number).upper()


def publication_record_id(publication_number: str) -> str:
    identity = {
        "patent.publication_number": normalize_publication_number(publication_number)
    }
    content = json.dumps(identity, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(content.encode()).hexdigest()


def output_path(output_dir: Path, output_index: int) -> Path:
    return output_dir / f"publication_numbers_not_in_mongo_{output_index:06d}.txt"


@contextmanager
def exclusive_filter_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    acquired = False
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise RuntimeError(
                f"Another filter process is using output directory: {lock_path.parent}"
            ) from exc
        acquired = True
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()).encode("ascii"))
        handle.flush()
        yield
    finally:
        try:
            if acquired:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            handle.close()


def initial_state(
    *,
    input_dir: Path,
    output_dir: Path,
    source_files: list[Path],
    database: str,
    collection: str,
    chunk_size: int,
) -> dict[str, Any]:
    return {
        "version": 1,
        "input_dir": str(input_dir.resolve()),
        "output_dir": str(output_dir.resolve()),
        "source_files": source_manifest(source_files),
        "database": database,
        "collection": collection,
        "batch_size": batch_size,
        "chunk_size": chunk_size,
        "current_file_index": 0,
        "current_line_number": 0,
        "output_index": 1,
        "output_lines": 0,
        "output_bytes": 0,
        "invalid_bytes": 0,
        "total_input_rows": 0,
        "total_existing": 0,
        "total_exported": 0,
        "total_invalid": 0,
    }


def load_state(state_path: Path) -> dict[str, Any] | None:
    if not state_path.exists():
        return None
    return json.loads(state_path.read_text(encoding="utf-8"))


def save_state(state_path: Path, state: dict[str, Any]) -> None:
    temporary_path = state_path.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    for attempt in range(20):
        try:
            os.replace(temporary_path, state_path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.1)


def validate_state(
    state: dict[str, Any],
    *,
    input_dir: Path,
    output_dir: Path,
    source_files: list[Path],
    database: str,
    collection: str,
    batch_size: int,
    chunk_size: int,
) -> None:
    expected = {
        "version": 1,
        "input_dir": str(input_dir.resolve()),
        "output_dir": str(output_dir.resolve()),
        "source_files": source_manifest(source_files),
        "database": database,
        "collection": collection,
        "chunk_size": chunk_size,
    }
    for key, value in expected.items():
        if state.get(key) != value:
            raise RuntimeError(
                f"Checkpoint {key}={state.get(key)!r}, current value is {value!r}"
            )


def open_resumable_binary(path: Path, size: int) -> BinaryIO:
    if path.exists():
        handle = path.open("r+b")
        actual_size = handle.seek(0, os.SEEK_END)
        if actual_size < size:
            handle.close()
            raise RuntimeError(
                f"Output file is shorter than checkpoint: {path} ({actual_size} < {size})"
            )
        handle.truncate(size)
        handle.seek(size)
        return handle
    if size:
        raise RuntimeError(f"Checkpoint expects an existing output file: {path}")
    return path.open("w+b")


def remove_uncheckpointed_output_files(output_dir: Path, output_index: int) -> None:
    for path in output_dir.glob("publication_numbers_not_in_mongo_*.txt"):
        try:
            file_index = int(path.stem.rsplit("_", 1)[1])
        except (IndexError, ValueError):
            continue
        if file_index > output_index:
            path.unlink()


def flush_and_checkpoint(
    *,
    state_path: Path,
    state: dict[str, Any],
    output_file: BinaryIO,
    invalid_file: BinaryIO,
) -> None:
    output_file.flush()
    invalid_file.flush()
    os.fsync(output_file.fileno())
    os.fsync(invalid_file.fileno())
    state["output_bytes"] = output_file.tell()
    state["invalid_bytes"] = invalid_file.tell()
    save_state(state_path, state)


def find_existing_record_ids(
    collection: Collection[Any], record_ids: list[str]
) -> set[str]:
    if not record_ids:
        return set()
    cursor = collection.find(
        {"_meta.record_id": {"$in": list(set(record_ids))}},
        {"_id": 0, "_meta.record_id": 1},
    )
    return {
        record_id
        for document in cursor
        if (record_id := document.get("_meta", {}).get("record_id"))
    }


def rotate_output(
    output_file: BinaryIO, output_dir: Path, state: dict[str, Any]
) -> BinaryIO:
    output_file.flush()
    os.fsync(output_file.fileno())
    output_file.close()
    state["output_index"] += 1
    state["output_lines"] = 0
    state["output_bytes"] = 0
    return output_path(output_dir, state["output_index"]).open("w+b")


def process_batch(
    *,
    batch: list[tuple[int, str]],
    source_name: str,
    collection: Collection[Any],
    output_dir: Path,
    chunk_size: int,
    state: dict[str, Any],
    output_file: BinaryIO,
    invalid_file: BinaryIO,
) -> BinaryIO:
    prepared: list[tuple[int, str, str | None]] = []
    record_ids: list[str] = []
    for line_number, raw_line in batch:
        publication_number = raw_line.strip()
        normalized = normalize_publication_number(publication_number)
        if not normalized:
            prepared.append((line_number, raw_line, None))
            continue
        record_id = publication_record_id(publication_number)
        prepared.append((line_number, publication_number, record_id))
        record_ids.append(record_id)

    existing_record_ids = find_existing_record_ids(collection, record_ids)
    for line_number, publication_number, record_id in prepared:
        state["total_input_rows"] += 1
        if record_id is None:
            invalid_file.write(
                f"{source_name}:{line_number}\t{publication_number.rstrip()}\n".encode()
            )
            state["total_invalid"] += 1
        elif record_id in existing_record_ids:
            state["total_existing"] += 1
        else:
            if state["output_lines"] >= chunk_size:
                output_file = rotate_output(output_file, output_dir, state)
            output_file.write(publication_number.encode("utf-8") + b"\n")
            state["output_lines"] += 1
            state["total_exported"] += 1
    state["current_line_number"] = batch[-1][0]
    return output_file


def filter_files(
    args: argparse.Namespace, collection: Collection[Any]
) -> dict[str, Any]:
    input_dir = args.input_dir.resolve()
    output_dir = args.output_dir.resolve()
    if args.batch_size <= 0:
        raise ValueError("--batch-size must be greater than zero")
    if args.chunk_size <= 0:
        raise ValueError("--chunk-size must be greater than zero")
    if not input_dir.is_dir():
        raise FileNotFoundError(f"Input directory does not exist: {input_dir}")
    if input_dir == output_dir:
        raise ValueError("--output-dir must be different from --input-dir")

    source_files = discover_source_files(input_dir)
    if not source_files:
        raise RuntimeError(f"No {SOURCE_PATTERN} files found directly under {input_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)
    state_path = output_dir / STATE_FILENAME
    invalid_path = output_dir / INVALID_FILENAME
    state = load_state(state_path)
    if state is None:
        existing_outputs = list(output_dir.glob("publication_numbers_not_in_mongo_*.txt"))
        if existing_outputs or invalid_path.exists():
            raise RuntimeError(
                f"Output exists without {STATE_FILENAME}; move it before a fresh run"
            )
        state = initial_state(
            input_dir=input_dir,
            output_dir=output_dir,
            source_files=source_files,
            database=settings.db_name,
            collection=args.collection,
            chunk_size=args.chunk_size,
        )
        save_state(state_path, state)
    else:
        validate_state(
            state,
            input_dir=input_dir,
            output_dir=output_dir,
            source_files=source_files,
            database=settings.db_name,
            collection=args.collection,
            batch_size=args.batch_size,
            chunk_size=args.chunk_size,
        )
        state["batch_size"] = args.batch_size

    if state.get("completed"):
        print(
            f"already completed rows={state['total_input_rows']} "
            f"existing={state['total_existing']} exported={state['total_exported']}",
            flush=True,
        )
        return state

    remove_uncheckpointed_output_files(output_dir, state["output_index"])
    output_file = open_resumable_binary(
        output_path(output_dir, state["output_index"]), state["output_bytes"]
    )
    invalid_file = open_resumable_binary(invalid_path, state["invalid_bytes"])
    print(
        f"files={len(source_files)} resume_file={state['current_file_index']} "
        f"resume_line={state['current_line_number']} output={output_dir}",
        flush=True,
    )

    try:
        for file_index in range(state["current_file_index"], len(source_files)):
            source_path = source_files[file_index]
            resume_line = (
                state["current_line_number"]
                if file_index == state["current_file_index"]
                else 0
            )
            print(
                f"[{file_index + 1}/{len(source_files)}] {source_path.name} "
                f"resume_line={resume_line}",
                flush=True,
            )
            batch: list[tuple[int, str]] = []
            with source_path.open("r", encoding="utf-8") as source:
                for line_number, raw_line in enumerate(source, start=1):
                    if line_number <= resume_line:
                        continue
                    batch.append((line_number, raw_line.rstrip("\r\n")))
                    if len(batch) < args.batch_size:
                        continue
                    output_file = process_batch(
                        batch=batch,
                        source_name=source_path.name,
                        collection=collection,
                        output_dir=output_dir,
                        chunk_size=args.chunk_size,
                        state=state,
                        output_file=output_file,
                        invalid_file=invalid_file,
                    )
                    state["current_file_index"] = file_index
                    flush_and_checkpoint(
                        state_path=state_path,
                        state=state,
                        output_file=output_file,
                        invalid_file=invalid_file,
                    )
                    batch.clear()
                    if state["total_input_rows"] % 100_000 == 0:
                        print(
                            f"rows={state['total_input_rows']} "
                            f"existing={state['total_existing']} "
                            f"exported={state['total_exported']} "
                            f"invalid={state['total_invalid']}",
                            flush=True,
                        )

                if batch:
                    output_file = process_batch(
                        batch=batch,
                        source_name=source_path.name,
                        collection=collection,
                        output_dir=output_dir,
                        chunk_size=args.chunk_size,
                        state=state,
                        output_file=output_file,
                        invalid_file=invalid_file,
                    )

            state["current_file_index"] = file_index + 1
            state["current_line_number"] = 0
            flush_and_checkpoint(
                state_path=state_path,
                state=state,
                output_file=output_file,
                invalid_file=invalid_file,
            )

        state["completed"] = True
        flush_and_checkpoint(
            state_path=state_path,
            state=state,
            output_file=output_file,
            invalid_file=invalid_file,
        )
    finally:
        output_file.close()
        invalid_file.close()

    print(
        f"completed rows={state['total_input_rows']} "
        f"existing={state['total_existing']} exported={state['total_exported']} "
        f"invalid={state['total_invalid']} txt_files={state['output_index']}",
        flush=True,
    )
    return state


def main() -> None:
    args = parse_args()
    if not settings.db_url or not settings.db_name:
        raise RuntimeError("SPIDER_DB_URL and SPIDER_DB_NAME must be configured")

    output_dir = args.output_dir.resolve()
    with exclusive_filter_lock(output_dir / LOCK_FILENAME):
        client: MongoClient[Any] = MongoClient(settings.db_url, appname="patent-txt-filter")
        try:
            client.admin.command("ping")
            collection = client[settings.db_name][args.collection]
            filter_files(args, collection)
        finally:
            client.close()


if __name__ == "__main__":
    main()
