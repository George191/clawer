#!/usr/bin/env python3
"""Start the Docker DNS service with the deployment host's LAN IPv4 address."""

import argparse
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys


def command_output(command: list[str]) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True)
    except FileNotFoundError as error:
        raise RuntimeError(f"Required command not found: {command[0]}") from error
    if result.returncode:
        raise RuntimeError(f"{command[0]} command failed (exit {result.returncode}).")
    return result.stdout


def detect_lan_ipv4() -> str:
    system = platform.system()
    if system == "Darwin":
        route = command_output(["route", "-n", "get", "default"])
        match = re.search(r"^\s*interface:\s*(\S+)\s*$", route, re.MULTILINE)
        if not match:
            raise RuntimeError("The default route has no network interface.")
        address = command_output(["ipconfig", "getifaddr", match.group(1)]).strip()
    elif system == "Linux":
        routes = json.loads(command_output(["ip", "-j", "-4", "route", "show", "default"]))
        if not routes:
            raise RuntimeError("No default IPv4 route was found.")
        route = min(routes, key=lambda item: item.get("metric", 0))
        interface = route.get("dev")
        if not interface:
            raise RuntimeError("The default route has no network interface.")
        interfaces = json.loads(command_output(["ip", "-j", "-4", "addr", "show", "dev", interface]))
        addresses = [
            item["local"]
            for network in interfaces
            for item in network.get("addr_info", [])
            if item.get("family") == "inet" and item.get("scope") == "global"
        ]
        if not addresses:
            raise RuntimeError("The default network interface has no global IPv4 address.")
        address = addresses[0]
    else:
        raise RuntimeError("Automatic LAN IPv4 detection supports macOS and Linux only.")
    try:
        return str(ipaddress.IPv4Address(address))
    except ipaddress.AddressValueError as error:
        raise RuntimeError("LAN IPv4 detection returned an invalid address.") from error


def dns_environment(compose_command: list[str]) -> dict:
    configuration = json.loads(command_output(compose_command + ["config", "--format", "json"]))
    service = configuration.get("services", {}).get("dns")
    if service is None:
        raise RuntimeError("The Compose configuration has no dns service.")
    return service.get("environment", {})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--compose-file", type=Path,
        default=Path(__file__).resolve().parents[1] / "docker-compose.yml",
    )
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    compose_command = ["docker", "compose", "--file", str(args.compose_file.resolve()), "--profile", "dns"]
    if args.env_file:
        compose_command += ["--env-file", str(args.env_file.resolve())]

    environment = dns_environment(compose_command)
    target = (environment.get("SPIDER_DNS_TARGET_IPV4") or "").strip()
    if target:
        try:
            target = str(ipaddress.IPv4Address(target))
        except ipaddress.AddressValueError as error:
            raise RuntimeError("SPIDER_DNS_TARGET_IPV4 must be a valid IPv4 address.") from error
    else:
        target = detect_lan_ipv4()

    print(f"Deploying Docker DNS with A records pointing to {target}.", flush=True)
    result = subprocess.run(
        compose_command + ["up", "-d", "--no-deps", "--force-recreate", "dns"],
        env={**os.environ, "SPIDER_DNS_TARGET_IPV4": target},
    )
    if result.returncode:
        raise RuntimeError(f"Docker DNS startup failed (exit {result.returncode}).")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, json.JSONDecodeError) as error:
        print(f"DNS deployment failed: {error}", file=sys.stderr)
        sys.exit(1)
