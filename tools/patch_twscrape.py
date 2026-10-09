"""Apply the local twscrape compatibility patches used by the crawler image."""

from pathlib import Path

import twscrape.models

OLD = '    if name == "745291183405076480:broadcast":\n'
NEW = '    if name in {"745291183405076480:broadcast", "3691233323:periscope_broadcast"}:\n'


def main() -> None:
    models_path = Path(twscrape.models.__file__)
    source = models_path.read_text(encoding="utf-8")
    if NEW in source:
        return
    if source.count(OLD) != 1:
        raise RuntimeError(f"Unexpected twscrape models.py layout: {models_path}")

    models_path.write_text(source.replace(OLD, NEW), encoding="utf-8")


if __name__ == "__main__":
    main()
