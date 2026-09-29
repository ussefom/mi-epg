"""Generate a safe XMLTV feed from the Movistar grid API using only stdlib."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import logging
import os
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

PLAYLIST_TIMEOUT_SECONDS = 30
REQUEST_TIMEOUT_SECONDS = 15
MAX_ATTEMPTS = 3
MAX_WORKERS = 4
EPG_ENDPOINT = "https://ottcache.dof6.com/movistarplus/lg/difusion/epg"
USER_AGENT = "ussefom-mi-epg/1.0"
COMPACT_MAX_BYTES = 40 * 1024
COMPACT_WINDOWS_HOURS = (24, 12)


@dataclass(frozen=True)
class Channel:
    code: str
    name: str
    logo: str | None = None


def download_bytes(url: str, timeout: int) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def decode_json(payload: bytes) -> Any:
    return json.loads(payload.decode("utf-8-sig"))


def extract_channels(playlist: Any) -> list[Channel]:
    """Return distinct, usable EPG IDs without retaining the source playlist."""
    if not isinstance(playlist, dict) or not isinstance(playlist.get("channels"), list):
        raise ValueError("playlist must be an object with a channels array")

    found: dict[str, Channel] = {}
    for item in playlist["channels"]:
        if not isinstance(item, dict):
            continue
        code = (
            item.get("tvg-id")
            or item.get("tvgId")
            or item.get("TvgId")
            or item.get("CodCadenaTv")
            or item.get("codCadenaTv")
            or item.get("cadenaTv")
        )
        if not isinstance(code, str) or not code.strip():
            continue
        code = code.strip()
        name = item.get("Nombre") or item.get("nombre") or code
        logo = item.get("Logo") or item.get("logo")
        found.setdefault(code, Channel(code=code, name=str(name).strip() or code, logo=logo if isinstance(logo, str) else None))
    if not found:
        raise ValueError("playlist contains no usable tvg-id or CodCadenaTv values")
    return sorted(found.values(), key=lambda channel: channel.code)


def endpoint_url(channel_code: str, start_day: datetime) -> str:
    query = urllib.parse.urlencode(
        {
            "from": start_day.strftime("%Y-%m-%dT00:00:00"),
            "span": "2",
            "channel": channel_code,
            "network": "movistarplus",
            "version": "8",
            "mdrm": "false",
            "tlsstream": "true",
        }
    )
    return f"{EPG_ENDPOINT}?{query}"


def fetch_channel(channel: Channel, start_day: datetime) -> list[dict[str, Any]]:
    url = endpoint_url(channel.code, start_day)
    last_error: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            data = decode_json(download_bytes(url, REQUEST_TIMEOUT_SECONDS))
            if not isinstance(data, list):
                raise ValueError("EPG response is not a list")
            return [
                event
                for event in flatten_events(data)
                if not isinstance(event.get("Canal"), dict)
                or event["Canal"].get("CodCadenaTv") == channel.code
            ]
        except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as error:
            last_error = error
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(2**attempt)
    assert last_error is not None
    raise RuntimeError(f"EPG request failed for channel {channel.code}") from last_error


def flatten_events(payload: Iterable[Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for item in payload:
        if isinstance(item, dict):
            events.append(item)
        elif isinstance(item, list):
            events.extend(event for event in item if isinstance(event, dict))
    return events


def xmltv_time(value: Any) -> str:
    milliseconds = int(value)
    if milliseconds <= 0:
        raise ValueError("timestamp must be positive")
    instant = datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc)
    return instant.strftime("%Y%m%d%H%M%S +0000")


def programme_element(channel_code: str, event: dict[str, Any]) -> ET.Element | None:
    try:
        start = xmltv_time(event["FechaHoraInicio"])
        stop = xmltv_time(event["FechaHoraFin"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if stop <= start:
        return None
    title = event.get("Titulo")
    if not isinstance(title, str) or not title.strip():
        return None
    programme = ET.Element("programme", {"start": start, "stop": stop, "channel": channel_code})
    ET.SubElement(programme, "title", {"lang": "es"}).text = title.strip()
    subtitle = event.get("TituloHorLinea2") or event.get("TituloEpisodio")
    if isinstance(subtitle, str) and subtitle.strip():
        ET.SubElement(programme, "sub-title", {"lang": "es"}).text = subtitle.strip()
    description = event.get("Descripcion") or event.get("Sinopsis")
    if isinstance(description, str) and description.strip():
        ET.SubElement(programme, "desc", {"lang": "es"}).text = description.strip()
    category = event.get("GeneroComAntena")
    if isinstance(category, str) and category.strip():
        ET.SubElement(programme, "category", {"lang": "es"}).text = category.strip()
    image = event.get("Imagen")
    if isinstance(image, str) and image.startswith(("https://", "http://")):
        ET.SubElement(programme, "icon", {"src": image})
    return programme


def channel_element(channel: Channel) -> ET.Element:
    element = ET.Element("channel", {"id": channel.code})
    ET.SubElement(element, "display-name", {"lang": "es"}).text = channel.name
    if channel.logo and channel.logo.startswith(("https://", "http://")):
        ET.SubElement(element, "icon", {"src": channel.logo})
    return element


def load_previous(url: str | None) -> dict[str, list[ET.Element]]:
    if not url:
        return {}
    try:
        raw = download_bytes(url, PLAYLIST_TIMEOUT_SECONDS)
        root = ET.fromstring(gzip.decompress(raw))
    except (OSError, ET.ParseError, urllib.error.URLError):
        return {}
    prior: dict[str, list[ET.Element]] = {}
    for element in root:
        channel_code = element.get("id") if element.tag == "channel" else element.get("channel")
        if channel_code:
            prior.setdefault(channel_code, []).append(element)
    return prior


def load_previous_sources(urls: Iterable[str]) -> dict[str, list[ET.Element]]:
    """Use the first valid prior XMLTV source, without exposing its URL."""
    for url in urls:
        prior = load_previous(url)
        if prior:
            return prior
    return {}


def build_feed(
    channels: list[Channel],
    fetcher: Callable[[Channel], list[dict[str, Any]]],
    previous: dict[str, list[ET.Element]] | None = None,
) -> tuple[ET.Element, dict[str, Any]]:
    previous = previous or {}
    root = ET.Element("tv", {"generator-info-name": "ussefom-mi-epg"})
    outcomes: dict[str, tuple[Channel, list[ET.Element] | None]] = {}
    failures = 0
    fresh_channels = 0
    fresh_programmes = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(fetcher, channel): channel for channel in channels}
        for future in as_completed(futures):
            channel = futures[future]
            try:
                programmes = []
                for event in future.result():
                    element = programme_element(channel.code, event)
                    if element is not None:
                        programmes.append(element)
                if not programmes:
                    raise ValueError("no valid programmes")
                outcomes[channel.code] = (channel, programmes)
                fresh_channels += 1
                fresh_programmes += len(programmes)
            except Exception:  # Deliberately do not expose upstream details in public status.
                outcomes[channel.code] = (channel, None)
                failures += 1

    if fresh_channels == 0:
        raise RuntimeError("no fresh valid EPG data; retaining the previous Pages deployment")

    published_channels = 0
    published_programmes = 0
    stale_channels = 0
    for channel in channels:
        _, programmes = outcomes[channel.code]
        if programmes is not None:
            root.append(channel_element(channel))
            root.extend(programmes)
            published_channels += 1
            published_programmes += len(programmes)
        elif channel.code in previous:
            root.extend(deepcopy(previous[channel.code]))
            published_channels += 1
            stale_channels += 1
            published_programmes += sum(1 for element in previous[channel.code] if element.tag == "programme")

    return root, {
        "fresh_channels": fresh_channels,
        "fresh_programmes": fresh_programmes,
        "failed_channels": failures,
        "stale_channels": stale_channels,
        "published_channels": published_channels,
        "published_programmes": published_programmes,
    }


def serialise_xml(root: ET.Element) -> bytes:
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def parse_xmltv_time(value: str) -> datetime:
    return datetime.strptime(value[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)


def compact_xml(
    root: ET.Element,
    now: datetime,
    hours: int,
) -> tuple[bytes, int]:
    """Render the minimum XMLTV needed by the TV for the next time window."""
    compact = ET.Element("tv", {"generator-info-name": "ussefom-mi-epg-compact"})
    for channel in root.findall("channel"):
        output_channel = ET.SubElement(compact, "channel", {"id": channel.get("id", "")})
        display_name = channel.find("display-name")
        if display_name is not None and display_name.text:
            ET.SubElement(output_channel, "display-name", display_name.attrib).text = display_name.text

    end = now + timedelta(hours=hours)
    programmes = 0
    for programme in root.findall("programme"):
        try:
            start = parse_xmltv_time(programme.attrib["start"])
        except (KeyError, ValueError):
            continue
        try:
            stop = parse_xmltv_time(programme.attrib["stop"])
        except (KeyError, ValueError):
            continue
        if stop <= now or start >= end:
            continue
        title = programme.find("title")
        if title is None or not title.text:
            continue
        output_programme = ET.SubElement(
            compact,
            "programme",
            {key: programme.attrib[key] for key in ("start", "stop", "channel")},
        )
        ET.SubElement(output_programme, "title", title.attrib).text = title.text
        programmes += 1
    if programmes == 0:
        raise RuntimeError("compact XMLTV contains no future programmes")
    return serialise_xml(compact), programmes


def build_compact_xml(
    root: ET.Element,
    now: datetime,
    max_bytes: int = COMPACT_MAX_BYTES,
    windows: tuple[int, ...] = COMPACT_WINDOWS_HOURS,
) -> tuple[bytes, int, int]:
    for hours in windows:
        xml, programmes = compact_xml(root, now, hours)
        if len(gzip.compress(xml, compresslevel=9, mtime=0)) <= max_bytes:
            return xml, hours, programmes
    raise RuntimeError("compact XMLTV exceeds the maximum gzip size")


def gzip_bytes(xml: bytes) -> bytes:
    return gzip.compress(xml, compresslevel=9, mtime=0)


def write_outputs(
    output_dir: Path,
    xml: bytes,
    compact: bytes,
    status: dict[str, Any],
    compact_hours: int,
    compact_programmes: int,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    full_gzip = gzip_bytes(xml)
    compact_gzip = gzip_bytes(compact)
    status = {
        **status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "full_gzip_bytes": len(full_gzip),
        "full_gzip_sha256": hashlib.sha256(full_gzip).hexdigest(),
        "compact_gzip_bytes": len(compact_gzip),
        "compact_gzip_sha256": hashlib.sha256(compact_gzip).hexdigest(),
        "compact_window_hours": compact_hours,
        "compact_programmes": compact_programmes,
    }
    files = {
        "movistar.xml.gz": full_gzip,
        "movistar-compact.xml.gz": compact_gzip,
        "status.json": json.dumps(status, ensure_ascii=False, indent=2).encode("utf-8") + b"\n",
    }
    for name, data in files.items():
        with tempfile.NamedTemporaryFile(dir=output_dir, delete=False) as temporary:
            temporary.write(data)
            temp_path = Path(temporary.name)
        temp_path.replace(output_dir / name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--playlist-url", default=os.environ.get("PLAYLIST_URL"))
    parser.add_argument("--previous-url", action="append", default=[])
    parser.add_argument("--output-dir", type=Path, default=Path("site"))
    arguments = parser.parse_args()
    if not arguments.playlist_url:
        parser.error("--playlist-url or PLAYLIST_URL is required")

    playlist = decode_json(download_bytes(arguments.playlist_url, PLAYLIST_TIMEOUT_SECONDS))
    channels = extract_channels(playlist)
    previous = load_previous_sources(arguments.previous_url)
    start_day = datetime.now(ZoneInfo("Europe/Madrid"))
    root, status = build_feed(channels, lambda channel: fetch_channel(channel, start_day), previous)
    now = datetime.now(timezone.utc)
    xml = serialise_xml(root)
    compact, compact_hours, compact_programmes = build_compact_xml(root, now)
    write_outputs(arguments.output_dir, xml, compact, status, compact_hours, compact_programmes)
    logging.info("published channels=%s programmes=%s fresh=%s failed=%s stale=%s", status["published_channels"], status["published_programmes"], status["fresh_channels"], status["failed_channels"], status["stale_channels"])
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    raise SystemExit(main())
