"""ODS rows for space catalogs, preserving source identity and uncertain values."""

from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from datetime import date, datetime
from typing import Any

from app.etl.normalizers import register_normalizer
from app.etl.normalizers.base import json_dumps, safe_date, safe_datetime, safe_str

_COMMON_FIELDS = ('record_id', 'data_source', 'data_type', 'source_id', 'url', 'description', 'captured_at', 'attributes')
_TYPE_FIELDS = {
    'constellation': ('name', 'status', 'purpose', 'owner', 'country', 'launched_count', 'planned_count', 'first_launch_date', 'first_launch_date_raw', 'member_norad_ids'),
    'debrisevents': ('parent_name', 'norad_id', 'event_type', 'event_date', 'event_date_raw', 'cataloged_object_count', 'perigee_km', 'apogee_km', 'inclination_degrees', 'cospar_id', 'cospar_launch', 'parent_gcat_id', 'related_objects'),
    'debris': ('name', 'norad_id', 'cospar_id', 'object_class', 'epoch', 'epoch_raw'),
    'launchvehicles': ('name', 'family', 'manufacturer', 'variant', 'manufacturer_codes'),
    'frequency': ('satellite_name', 'norad_id', 'notice_id', 'direction', 'frequency_from_hz', 'frequency_to_hz', 'uplink_low_hz', 'uplink_high_hz', 'downlink_low_hz', 'downlink_high_hz', 'cospar_id', 'satnogs_sat_id', 'itu_earth_notice_id'),
    'groundstations': ('name', 'organization', 'country', 'latitude', 'longitude', 'altitude_m', 'status', 'bands', 'location', 'callsign', 'itu_earth_notice_id'),
    'launchinfo': ('mission_name', 'rocket_name', 'provider', 'site_name', 'pad_name', 'status', 'cospar_launch', 'launch_date', 'launch_date_raw', 'site_code', 'provider_codes', 'vehicle_variant'),
    'satellite': ('name', 'norad_id', 'cospar_id', 'alternative_name', 'owner', 'country', 'manufacturer', 'status', 'launch_date', 'launch_date_raw', 'rocket_name', 'site_name', 'orbit_class', 'perigee_km', 'apogee_km', 'inclination_degrees', 'period_minutes', 'launch_mass_kg', 'dry_mass_kg', 'tle_line1', 'tle_line2', 'gcat_id', 'owner_codes', 'manufacturer_codes', 'cospar_launch', 'satnogs_sat_id'),
    'launchsites': ('name', 'country', 'site_code', 'pad_name', 'site_type', 'latitude', 'longitude', 'location', 'operational_start_raw', 'operational_stop_raw', 'operator_codes'),
    'orbital': ('name', 'norad_id', 'cospar_id', 'epoch', 'epoch_raw', 'tle_line0', 'tle_line1', 'tle_line2', 'inclination_degrees', 'eccentricity', 'mean_motion', 'mean_anomaly_degrees', 'ra_of_asc_node_degrees', 'arg_of_pericenter_degrees'),
    'organization': ('name', 'code', 'country', 'organization_type', 'parent', 'parent_codes', 'latitude', 'longitude', 'operational_start_raw', 'operational_stop_raw'),
}
_TYPE_FIELDS["organization"] += (
    "cik", "entity_type", "exchanges", "tickers", "sic", "sic_description", "address", "website",
    "ein", "category", "phone", "former_names", "investor_website", "fiscal_year_end", "incorporation_state",
)
_INTEGER_FIELDS = frozenset(('cataloged_object_count', 'launched_count', 'norad_id', 'planned_count'))
_NUMBER_FIELDS = frozenset(('altitude_m', 'apogee_km', 'arg_of_pericenter_degrees', 'downlink_high_hz', 'downlink_low_hz', 'dry_mass_kg', 'eccentricity', 'frequency_from_hz', 'frequency_to_hz', 'inclination_degrees', 'latitude', 'launch_mass_kg', 'longitude', 'mean_anomaly_degrees', 'mean_motion', 'perigee_km', 'period_minutes', 'ra_of_asc_node_degrees', 'uplink_high_hz', 'uplink_low_hz'))

_INTERNAL_FIELDS = {"_id", "_meta", "_kafka_meta", "_meta_search_params", "assets"}


def _text(value: Any) -> str | None:
    return safe_str(value) if not isinstance(value, dict | list | tuple | bool) else None


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).strip())
    except (ValueError, TypeError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _integer(value: Any) -> int | None:
    text = _text(value) or ""
    if not re.fullmatch(r"[-+]?\d+(?:\.0+)?", text):
        return None
    result = int(text.split(".")[0])
    return result if -(2 ** 63) <= result < 2 ** 63 else None


def _norad(value: Any) -> int | None:
    number = _integer(value)
    return number if number is not None and number > 0 else None


def _cospar(value: Any) -> str | None:
    value = (_text(value) or "").upper()
    return value if re.fullmatch(r"[0-9]{4}-[0-9]{3}[A-Z]+", value) else None


def _cospar_launch(value: Any) -> str | None:
    value = (_text(value) or "").upper()
    return value[:8] if re.fullmatch(r"[0-9]{4}-[0-9]{3}(?:[A-Z]+)?", value) else None


def _codes(value: Any) -> str | None:
    value = _text(value)
    if value is None or value.lower() in {"-", "?", "n/a", "none", "null", "unknown"}:
        return None
    codes = [code.strip() for code in value.split("/")
             if code.strip().lower() not in {"", "-", "?", "n/a", "none", "null", "unknown"}]
    return json_dumps(list(dict.fromkeys(codes)))


def _day(value: Any) -> date | None:
    # A year, month, question mark or range never becomes a fabricated day.
    if isinstance(value, date | datetime):
        return safe_date(value)
    text = _text(value) or ""
    gcat = re.fullmatch(r"(\d{4})\s+([A-Za-z]{3})\s+(\d{1,2})(?:\s+\d{2}:?\d{2}(?::\d{2}(?:\.\d+)?)?)?", text)
    if gcat:
        try:
            return datetime.strptime(" ".join(gcat.groups()), "%Y %b %d").date()
        except ValueError:
            return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?", text):
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        except ValueError:
            return None
    if re.fullmatch(r"\d{1,2} [A-Za-z]{3,9} \d{4}|[A-Za-z]{3,9} \d{1,2}, \d{4}", text):
        parsed = safe_datetime(text)
        return parsed.date() if parsed else None
    return safe_date(text) if re.fullmatch(r"\d{4}/\d{2}/\d{2}|\d{8}", text) else None


def _epoch(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None and value.utcoffset() is not None else None
    text = _text(value) or ""
    return safe_datetime(text) if re.search(r"(?:Z|[+-]\d{2}:?\d{2})$", text) else None


def _business(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _business(child) for key, child in value.items() if key not in _INTERNAL_FIELDS}
    if isinstance(value, list):
        return [_business(child) for child in value]
    return value


class _Row:
    def __init__(self, record: dict[str, Any], kind: str) -> None:
        self.remaining = _business(deepcopy(record))
        self.values = dict.fromkeys((*_COMMON_FIELDS, *_TYPE_FIELDS[kind]))
        meta = record.get("_meta") or {}
        self.sources = tuple(str(value).lower() for value in
                             (record.get("source"), record.get("data_source")) if value)
        gcat_kinds = ("satellite", "organization", "launchinfo", "launchsites", "launchvehicles", "debrisevents")
        self.is_gcat = any(source.startswith(("jonathan", *(name + "_jonathan" for name in gcat_kinds)))
                           for source in self.sources)
        self.is_satnogs = any(source.startswith(("satnogs", "satellite_satnogs", "frequency_satnogs"))
                              for source in self.sources)
        self.values.update(record_id=meta.get("record_id"),
                           data_source=safe_str(record.get("data_source")),
                           data_type=kind, captured_at=safe_datetime(meta.get("updated_at")))
        for key in ("record_id", "data_source", "data_type"):
            self.remaining.pop(key, None)
        self.take("source_id", "source_id")
        self.take("url", "url", "info_source", "source_url")
        self.take("description", "description", "launch_description", "constellation_info", "notes")

    def take(self, target: str, *paths: str, cast=None) -> None:
        if cast is None:
            cast = (_norad if target == "norad_id" else _cospar if target == "cospar_id"
                    else _cospar_launch if target == "cospar_launch"
                    else _integer if target in _INTEGER_FIELDS else _number if target in _NUMBER_FIELDS
                    else json_dumps if target == "bands" else _text)
        for path in paths:
            parent = self.remaining
            components = path.split(".")
            parents = []
            for key in components[:-1]:
                if not isinstance(parent.get(key), dict):
                    break
                parents.append((parent, key))
                parent = parent[key]
            else:
                key = components[-1]
                if key not in parent or parent[key] is None:
                    continue
                value = cast(parent[key])
                if value is None:
                    continue  # Keep unparsed values in attributes.
                self.values[target] = value
                del parent[key]
                for owner, component in reversed(parents):
                    if not owner[component]:
                        del owner[component]
                return

    def dated(self, target: str, *paths: str, epoch: bool = False) -> None:
        self.take(target + "_raw", *paths)
        self.values[target] = (_epoch if epoch else _day)(self.values[target + "_raw"])

    def finish(self) -> dict[str, Any]:
        self.values["attributes"] = json.dumps(self.remaining, ensure_ascii=False, default=str)
        return self.values


def normalize_constellation(record: dict[str, Any]) -> dict[str, Any]:
    row = _Row(record, "constellation")
    for target, paths in {
        "name": ("constellation_name", "name"), "status": ("constellation_status", "status"),
        "purpose": ("purpose",), "owner": ("owner", "owner_info.owner"),
        "country": ("country", "owner_info.country"), "launched_count": ("launched", "launched_count"),
        "planned_count": ("planned", "planned_count"),
    }.items():
        row.take(target, *paths)
    row.dated("first_launch_date", "first_launch_date", "first_launch")
    members = record.get("sats_included")
    if not isinstance(members, list):
        members = record.get("satellites")
    if isinstance(members, list):
        norads = [_norad(member.get("norad_id") if isinstance(member, dict) else member) for member in members]
        row.values["member_norad_ids"] = json_dumps(list(dict.fromkeys(value for value in norads if value is not None)))
    return row.finish()


def normalize_debrisevents(record: dict[str, Any]) -> dict[str, Any]:
    row = _Row(record, "debrisevents")
    for target, paths in {
        "parent_name": ("parent_name", "debris.parent_name", "name"), "norad_id": ("norad_id",),
        "event_type": ("event_type", "debris.event_type"),
        "cataloged_object_count": ("cataloged_object_count", "debris.cataloged_object_count"),
        "perigee_km": ("event_perigee_km", "debris.event_perigee_km"),
        "apogee_km": ("event_apogee_km", "debris.event_apogee_km"),
        "inclination_degrees": ("event_inclination_degrees", "debris.event_inclination_degrees"),
    }.items():
        row.take(target, *paths)
    row.take("cospar_id", "cospar_id", "launch_designation", "debris.launch_designation")
    row.values["cospar_launch"] = _cospar_launch(row.values["cospar_id"])
    if row.values["cospar_launch"] is None:
        row.take("cospar_launch", "launch_designation", "debris.launch_designation")
    if row.is_gcat:
        row.take("parent_gcat_id", "parent_jonathan_catid", "debris.parent_jonathan_catid")
    if any(source.startswith(("esa_discos", "debrisevents_esa_discos")) for source in row.sources):
        related_objects = record.get("related_objects")
        if isinstance(related_objects, list):
            row.values["related_objects"] = json_dumps(related_objects)
            row.remaining.pop("related_objects", None)
    row.dated("event_date", "event_date", "debris.event_date", "epoch")
    return row.finish()


def normalize_debris(record: dict[str, Any]) -> dict[str, Any]:
    row = _Row(record, "debris")
    for field in ("name", "norad_id", "cospar_id", "object_class"):
        row.take(field, field)
    row.dated("epoch", "epoch", epoch=True)
    if row.values["epoch"] is None and record.get("time_system") == "UTC":
        row.values["epoch"] = safe_datetime(row.values["epoch_raw"])
    return row.finish()


def normalize_launchvehicles(record: dict[str, Any]) -> dict[str, Any]:
    row = _Row(record, "launchvehicles")
    for target, paths in {
        "name": ("rocket_name", "name"), "family": ("lv_family", "family"),
        "manufacturer": ("lv_manufacturer", "manufacturer"), "variant": ("lv_variant", "variant"),
    }.items():
        row.take(target, *paths)
    if row.is_gcat:
        row.values["manufacturer_codes"] = _codes(record.get("lv_manufacturer"))
    # GCAT/DISCOS mass and dimensions have different source units; retain them unchanged.
    return row.finish()


def normalize_frequency(record: dict[str, Any]) -> dict[str, Any]:
    row = _Row(record, "frequency")
    for target, paths in {
        "satellite_name": ("satellite_name",), "norad_id": ("norad_id",), "cospar_id": ("cospar_id",),
        "notice_id": ("ntc_id", "notice_id"), "direction": ("emi_rcp", "direction"),
    }.items():
        row.take(target, *paths)
    if row.is_satnogs:
        row.take("satnogs_sat_id", "sat_id")
    com_el = record.get("com_el")
    notice_type = record.get("ntc_type") or (com_el.get("ntc_type") if isinstance(com_el, dict) else None)
    if any(source in {"itu_wic", "frequency_itu_wic"} for source in row.sources) and notice_type == "S":
        row.values["itu_earth_notice_id"] = _text(record.get("ntc_id"))
    unit = str(record.get("frequency_unit") or "").strip().lower()
    multiplier = {"hz": 1, "khz": 1000, "mhz": 1_000_000, "ghz": 1_000_000_000}.get(unit)
    row.take("frequency_from_hz", "frequency_from_hz")
    row.take("frequency_to_hz", "frequency_to_hz")
    if multiplier is not None:
        for target, source in (("frequency_from_hz", "freq_from"), ("frequency_to_hz", "freq_to")):
            if row.values[target] is None:
                row.take(target, source, cast=lambda value: number * multiplier if (number := _number(value)) is not None else None)
    # SatNOGS documents these four native fields in Hz; no unit is inferred for other sources.
    source = str(record.get("source") or record.get("data_source") or "")
    for field in ("uplink_low", "uplink_high", "downlink_low", "downlink_high"):
        row.take(field + "_hz", field + "_hz", *([field] if source.startswith("satnogs") else []))
    return row.finish()


def normalize_groundstations(record: dict[str, Any]) -> dict[str, Any]:
    row = _Row(record, "groundstations")
    for target, paths in {
        "name": ("stn_name", "station_name", "name"), "organization": ("organization",),
        "country": ("country",), "latitude": ("lat_dec", "latitude"),
        "longitude": ("long_dec", "longitude"), "status": ("status",), "bands": ("bands",),
        "location": ("location",), "callsign": ("callsign",),
    }.items():
        row.take(target, *paths)
    if any(source.startswith(("itu_", "groundstations_itu_")) for source in row.sources):
        row.take("itu_earth_notice_id", "ntc_id")
    row.take("altitude_m", "altitude_m")
    if str(record.get("data_source") or "").startswith("starlink"):
        row.take("altitude_m", "alt")  # KML Point altitude is explicitly meters.
    return row.finish()


def normalize_launchinfo(record: dict[str, Any]) -> dict[str, Any]:
    row = _Row(record, "launchinfo")
    for target, paths in {
        "mission_name": ("mission_name", "name"), "rocket_name": ("rocket_name", "launch_vehicle"),
        "provider": ("provider",), "site_name": ("site_name", "launch_site"), "pad_name": ("pad_name",),
        "status": ("status_name", "launch_status", "status"), "cospar_launch": ("cospar_launch",),
    }.items():
        row.take(target, *paths)
    if row.is_gcat:
        row.values["site_code"] = _text(record.get("site_name"))
        row.values["provider_codes"] = _codes(record.get("provider"))
        row.take("vehicle_variant", "lv_variant")
    row.dated("launch_date", "launch_date_text", "launch_date", "date_str")
    return row.finish()


def normalize_satellite(record: dict[str, Any]) -> dict[str, Any]:
    row = _Row(record, "satellite")
    for target, paths in {
        "name": ("satellite_name", "name"), "norad_id": ("norad_id",), "cospar_id": ("cospar_id",),
        "alternative_name": ("alternative_name",), "owner": ("owner", "detail_organisation"),
        "country": ("country_of_owner", "country_of_registry", "country"), "manufacturer": ("manufacturer",),
        "status": ("detail_status", "status"), "rocket_name": ("rocket_name", "launch_vehicle", "launch_info.launch_vehicle"),
        "site_name": ("site_name", "launch_site", "launch_info.launch_site"),
        "orbit_class": ("orbit_class", "orbit_info.orbit_class"),
        "perigee_km": ("perigee_km", "orbit_info.perigee_km"),
        "apogee_km": ("apogee_km", "orbit_info.apogee_km"),
        "inclination_degrees": ("inclination_degrees", "orbit_info.inclination_degrees", "orbit_info.incl_degrees"),
        "period_minutes": ("period_minutes", "orbit_info.period_minutes"),
        "launch_mass_kg": ("launch_mass_kg", "launch_info.launch_mass_kg"),
        "dry_mass_kg": ("dry_mass_kg", "launch_info.dry_mass_kg"),
        "tle_line1": ("tle_line1", "tle1", "orbit_info.tle1"),
        "tle_line2": ("tle_line2", "tle2", "orbit_info.tle2"),
    }.items():
        row.take(target, *paths, **({"cast": lambda value: value if isinstance(value, str) else None}
                                  if target.startswith("tle_line") else {}))
    row.dated("launch_date", "launch_date_text", "launch_date", "launch_info.launch_date")
    row.values["cospar_launch"] = _cospar_launch(row.values["cospar_id"])
    if row.is_gcat:
        if row.values["cospar_launch"] is None:
            row.take("cospar_launch", "launch_tag")
        row.take("gcat_id", "jonathan_catid")
        row.values["owner_codes"] = _codes(record.get("owner"))
        row.values["manufacturer_codes"] = _codes(record.get("manufacturer"))
    if row.is_satnogs:
        row.take("satnogs_sat_id", "sat_id")
        if row.values["satnogs_sat_id"] is None:
            row.values["satnogs_sat_id"] = row.values["source_id"]
    return row.finish()


def normalize_launchsites(record: dict[str, Any]) -> dict[str, Any]:
    row = _Row(record, "launchsites")
    for target, paths in {
        "name": ("site_name", "name"), "country": ("country",), "site_code": ("site_code",),
        "pad_name": ("pad_name",), "site_type": ("site_type",), "latitude": ("latitude", "lat_dec"),
        "longitude": ("longitude", "long_dec"), "location": ("location",),
        "operational_start_raw": ("operational_start", "operational_date"),
        "operational_stop_raw": ("operational_stop",),
    }.items():
        row.take(target, *paths)
    if row.is_gcat:
        row.values["operator_codes"] = _codes(record.get("parent_org"))
    return row.finish()


def normalize_orbital(record: dict[str, Any]) -> dict[str, Any]:
    row = _Row(record, "orbital")
    for target, paths in {
        "name": ("name", "object_name"), "norad_id": ("norad_id",), "cospar_id": ("cospar_id",),
        "tle_line0": ("tle_line0",), "tle_line1": ("tle_line1", "tle1"), "tle_line2": ("tle_line2", "tle2"),
        "inclination_degrees": ("inclination_degrees", "inclination"), "eccentricity": ("eccentricity",),
        "mean_motion": ("mean_motion",), "mean_anomaly_degrees": ("mean_anomaly_degrees", "mean_anomaly"),
        "ra_of_asc_node_degrees": ("ra_of_asc_node_degrees", "ra_of_asc_node", "raan"),
        "arg_of_pericenter_degrees": ("arg_of_pericenter_degrees", "arg_of_pericenter", "argument_of_perigee"),
    }.items():
        row.take(target, *paths, **({"cast": lambda value: value if isinstance(value, str) else None}
                                  if target.startswith("tle_line") else {}))
    row.dated("epoch", "epoch", epoch=True)
    if row.values["epoch"] is None and record.get("time_system") == "UTC":
        row.values["epoch"] = safe_datetime(row.values["epoch_raw"])
    return row.finish()


def normalize_organization(record: dict[str, Any]) -> dict[str, Any]:
    row = _Row(record, "organization")
    for target, paths in {
        "name": ("name", "e_name", "short_name"), "code": ("code",), "country": ("state_code", "country"),
        "organization_type": ("type", "organization_type"), "parent": ("parent",),
        "latitude": ("latitude",), "longitude": ("longitude",),
        "operational_start_raw": ("t_start", "operational_start"),
        "operational_stop_raw": ("t_stop", "operational_stop"),
        "fiscal_year_end": ("fiscal_year_end",), "incorporation_state": ("incorporation_state",),
    }.items():
        row.take(target, *paths)
    if row.is_gcat:
        row.values["parent_codes"] = _codes(record.get("parent"))
    return row.finish()


for _kind in _TYPE_FIELDS:
    register_normalizer(_kind, "*", globals()["normalize_" + _kind])
