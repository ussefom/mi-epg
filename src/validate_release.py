"""Validate the XMLTV assets before a GitHub Release is published."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

ASSET_NAMES = ("movistar.xml.gz", "movistar-compact.xml.gz", "status.json")
COMPACT_MAX_BYTES = 40 * 1024


def parse_xmltv(path: Path) -> ET.Element:
    root = ET.fromstring(gzip.decompress(path.read_bytes()))
    if root.tag != "tv":
        raise ValueError(f"{path.name} does not contain an XMLTV document")
    if not root.findall("channel") or not root.findall("programme"):
        raise ValueError(f"{path.name} contains no channels or programmes")
    return root


def validate_assets(directory: Path, required_channels: set[str]) -> dict[str, int]:
    paths = {name: directory / name for name in ASSET_NAMES}
    missing = [name for name, path in paths.items() if not path.is_file()]
    if missing:
        raise ValueError(f"missing release assets: {', '.join(missing)}")

    full = parse_xmltv(paths["movistar.xml.gz"])
    compact = parse_xmltv(paths["movistar-compact.xml.gz"])
    status = json.loads(paths["status.json"].read_text(encoding="utf-8"))
    for name, path in (("full", paths["movistar.xml.gz"]), ("compact", paths["movistar-compact.xml.gz"])):
        data = path.read_bytes()
        if status[f"{name}_gzip_bytes"] != len(data):
            raise ValueError(f"status size mismatch for {name}")
        if status[f"{name}_gzip_sha256"] != hashlib.sha256(data).hexdigest():
            raise ValueError(f"status checksum mismatch for {name}")
    if len(paths["movistar-compact.xml.gz"].read_bytes()) > COMPACT_MAX_BYTES:
        raise ValueError("compact XMLTV exceeds the 40 KiB limit")
    if status["compact_window_hours"] not in (24, 12):
        raise ValueError("compact XMLTV uses an unsupported time window")
    found_ids = {channel.get("id") for channel in full.findall("channel")}
    missing_ids = required_channels - found_ids
    if missing_ids:
        raise ValueError(f"required XMLTV channels are absent: {', '.join(sorted(missing_ids))}")
    return {
        "full_channels": len(full.findall("channel")),
        "full_programmes": len(full.findall("programme")),
        "compact_channels": len(compact.findall("channel")),
        "compact_programmes": len(compact.findall("programme")),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--require-channel", action="append", default=[])
    arguments = parser.parse_args()
    summary = validate_assets(arguments.directory, set(arguments.require_channel))
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
