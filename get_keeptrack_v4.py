"""
Reference: old KeepTrack downloader script

KeepTrack v4 version.

Script flow:
1. Periodically download raw satellite data from `/v4/sats/celestrak`
2. Save the raw JSON snapshot
3. Transform data into the target satellite JSON format

Field usage is strict:
- only uses documented `/v4/sats/celestrak` fields
- no alias fallback
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import time
from logging.handlers import RotatingFileHandler
from typing import Any

import requests
import yaml

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
except ImportError:  # pragma: no cover
    FileSystemEventHandler = object  # type: ignore[assignment]
    Observer = None


class ConfigHandler(FileSystemEventHandler):
    """Handle config file modification events."""

    def __init__(self, config_file: str, callback: Any) -> None:
        self.config_file = config_file
        self.callback = callback

    def on_modified(self, event: Any) -> None:
        if not event.is_directory and event.src_path == os.path.abspath(self.config_file):
            self.callback()


class KeepTrackDataDownloader:
    """Download KeepTrack v4 data and transform it to the target JSON shape."""

    def __init__(self, config_file: str | None = None, logger: logging.Logger | None = None):
        self.config_file = config_file
        self.logger = logger
        self.load_config()

        if self.logger is None:
            self.setup_logging()

        self.setup_directories()
        if self.config_file:
            self.setup_config_watch()

    def setup_logging(self) -> None:
        """Configure logging."""
        self.logger = logging.getLogger("keeptrack_downloader")
        self.logger.setLevel(logging.INFO)
        self.logger.handlers.clear()

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
        log_filename = os.path.join(
            self.config["keeptrack_paths"]["log_dir"],
            f"{timestamp}_keeptrack.log",
        )

        file_handler = RotatingFileHandler(
            log_filename,
            maxBytes=self.config["logging"]["max_bytes"],
            backupCount=self.config["logging"]["backup_count"],
            encoding="utf-8",
        )
        file_handler.setLevel(logging.INFO)

        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)

        formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)

        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)

    def setup_directories(self) -> None:
        """Create required directories."""
        for dir_path in [
            self.config["keeptrack_paths"]["init_save"],
            self.config["keeptrack_paths"]["save_json"],
            self.config["keeptrack_paths"]["log_dir"],
        ]:
            os.makedirs(dir_path, exist_ok=True)

    def setup_config_watch(self) -> None:
        """Watch config file changes."""
        if Observer is None:
            return

        self.observer = Observer()
        handler = ConfigHandler(self.config_file, self.load_config)
        self.observer.schedule(
            handler,
            path=os.path.dirname(os.path.abspath(self.config_file)),
            recursive=False,
        )
        self.observer.start()

    def load_config(self) -> None:
        """Load YAML config file."""
        try:
            with open(self.config_file, "r", encoding="utf-8") as file_obj:
                self.config = yaml.safe_load(file_obj)
            if hasattr(self, "logger") and self.logger:
                self.logger.info("KeepTrack config loaded successfully.")
        except Exception as exc:
            error_msg = f"KeepTrack config load failed: {exc}"
            if hasattr(self, "logger") and self.logger:
                self.logger.error(error_msg)
            else:
                print(error_msg)
            raise

    @staticmethod
    def convert_datetime(date_str: Any) -> str | None:
        """Convert ISO datetime string to ISO format."""
        if not date_str:
            return None

        text = str(date_str).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"

        try:
            return datetime.datetime.fromisoformat(text).isoformat()
        except ValueError:
            return None

    @staticmethod
    def convert_launch_date(date_str: Any) -> str | None:
        """Convert ISO datetime string to YYYY-MM-DD."""
        if not date_str:
            return None

        text = str(date_str).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"

        try:
            return datetime.datetime.fromisoformat(text).date().isoformat()
        except ValueError:
            return None

    @staticmethod
    def extract_norad_id(tle1: Any) -> int | None:
        """Extract NORAD id from TLE line 1."""
        try:
            if not tle1:
                return None
            return int(str(tle1)[2:7])
        except Exception:
            return None

    @staticmethod
    def convert_to_float(value: Any) -> float | None:
        """Convert value to float."""
        try:
            if value in (None, ""):
                return None
            return float(value)
        except Exception:
            return None

    def transform_satellite_data(self, raw_data: list[dict[str, Any]]) -> dict[str, Any]:
        """
        Transform data using strict documented `/v4/sats/celestrak` fields.

        `/v4/sats/celestrak` fields used:
        - tle1
        - tle2
        - payload
        - Mass
        - vmag
        - launchDate
        - owner
        - country
        - manufacturer
        - bus
        - launchMass
        - dryMass
        - length
        - diameter
        - span
        - shape
        - name
        - altName
        - status
        - type
        - rcs
        - stableDate
        - launchSite
        - launchVehicle
        - launchPad
        """
        transformed = {"satellite": []}

        for sat in raw_data:
            norad_id = self.extract_norad_id(sat.get("tle1"))

            satellite = {
                "update_time": datetime.datetime.now().isoformat(),
                "satellite_name": sat.get("name"),
                "alternative_name": sat.get("altName"),
                "configuration": None,
                "country_of_registry": sat.get("country"),
                "owner": sat.get("owner"),
                "equipment": None,
                "norad_id": norad_id,
                "norad_type": str(sat.get("type")) if sat.get("type") is not None else None,
                "manufacturer": sat.get("manufacturer"),
                "length": sat.get("length"),
                "diameter": sat.get("diameter"),
                "span": sat.get("span"),
                "shape": sat.get("shape"),
                "orbit_info": {
                    "rcs": sat.get("rcs"),
                    "deployed_date": self.convert_datetime(sat.get("stableDate")),
                    "tle1": sat.get("tle1"),
                    "tle2": sat.get("tle2"),
                },
                "launch_info": {
                    "launch_mass_kg": self.convert_to_float(sat.get("launchMass")),
                    "dry_mass_kg": self.convert_to_float(sat.get("dryMass")),
                    "launch_date": self.convert_launch_date(sat.get("launchDate")),
                    "launch_site": sat.get("launchSite"),
                    "launch_vehicle": sat.get("launchVehicle"),
                    "launch_pad": sat.get("launchPad"),
                },
            }
            transformed["satellite"].append(satellite)

        return transformed

    def download_keeptrack_data(self) -> bool:
        """Download KeepTrack v4 raw data and save transformed output."""
        list_url = self.config["keeptrack_api"]["url"]
        headers = {
            "User-Agent": self.config["keeptrack_api"]["user_agent"],
            "X-API-Key": self.config["keeptrack_api"]["api_key"],
            "Accept": "application/json",
        }

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        raw_filename = os.path.join(
            self.config["keeptrack_paths"]["init_save"],
            f"{timestamp}_keeptrack.json",
        )
        transformed_filename = os.path.join(
            self.config["keeptrack_paths"]["save_json"],
            f"{timestamp}_keeptrack.json",
        )

        max_retries = self.config["keeptrack_api"]["max_retry"]
        retry_delay = self.config["keeptrack_api"]["retry_interval"]
        timeout = self.config["keeptrack_api"]["timeout"]

        for attempt in range(max_retries):
            try:
                response = requests.get(list_url, headers=headers, timeout=timeout)
                response.raise_for_status()
                raw_data = response.json()

                if not isinstance(raw_data, list):
                    raise TypeError("KeepTrack list response must be a list.")

                with open(raw_filename, "w", encoding="utf-8") as file_obj:
                    json.dump(raw_data, file_obj, ensure_ascii=False, indent=4)

                file_size = os.path.getsize(raw_filename)
                self.logger.info(
                    f"KeepTrack raw data [{file_size} bytes] saved to: {raw_filename}"
                )

                transformed_data = self.transform_satellite_data(raw_data)
                with open(transformed_filename, "w", encoding="utf-8") as file_obj:
                    json.dump(transformed_data, file_obj, ensure_ascii=False, indent=4)

                self.logger.info(
                    f"KeepTrack transformed data saved to: {transformed_filename}"
                )
                return True

            except Exception as exc:
                self.logger.error(
                    f"KeepTrack download attempt {attempt + 1}/{max_retries} failed: {exc}"
                )
                if attempt < max_retries - 1:
                    self.logger.info(f"KeepTrack retry after {retry_delay} seconds...")
                    time.sleep(retry_delay)
                    retry_delay *= 2

        self.logger.error("KeepTrack all download attempts failed.")
        return False

    def start_monitoring(self, blocking: bool = False) -> None:
        """Start periodic download task."""
        self.logger.info("KeepTrack start periodic download task.")
        try:
            if blocking:
                self.run()
            else:
                import threading

                self.monitor_thread = threading.Thread(target=self.run, daemon=True)
                self.monitor_thread.start()
        except Exception as exc:
            self.logger.error(f"KeepTrack failed to start periodic download: {exc}")
            raise

    def stop_monitoring(self) -> None:
        """Stop monitoring."""
        if hasattr(self, "observer"):
            self.observer.stop()
            self.observer.join()
        self.logger.info("KeepTrack stop periodic download task.")

    def run(self) -> None:
        """Run periodic download loop."""
        self.logger.info("KeepTrack start periodic downloader loop.")
        try:
            while True:
                if self.download_keeptrack_data():
                    self.logger.info(
                        f"KeepTrack next download will run after "
                        f"{self.config['keeptrack_api']['get_freq']} day(s)."
                    )
                    time.sleep(self.config["keeptrack_api"]["get_freq"] * 24 * 3600)
                else:
                    self.logger.error("KeepTrack download failed, retry after 1 hour.")
                    time.sleep(3600)
        except KeyboardInterrupt:
            self.logger.info("KeepTrack received stop signal, exiting...")
            self.stop_monitoring()


def main_get_keeptrack(
    config_file_path: str | None = None,
    logger: logging.Logger | None = None,
    blocking: bool = False,
) -> KeepTrackDataDownloader:
    """Main entry."""
    downloader = KeepTrackDataDownloader(config_file=config_file_path, logger=logger)
    downloader.start_monitoring(blocking=blocking)
    return downloader


if __name__ == "__main__":
    args_blocking = False
    try:
        downloader = main_get_keeptrack(
            config_file_path="sat_config.yaml",
            blocking=args_blocking,
        )

        if not args_blocking:
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                print("\nKeepTrack received exit signal, stopping...")
                downloader.stop_monitoring()

    except Exception as exc:
        print(f"KeepTrack startup failed: {exc}")
        raise
