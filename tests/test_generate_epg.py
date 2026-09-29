import gzip
import json
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from src.generate_epg import (
    Channel,
    build_compact_xml,
    build_feed,
    compact_xml,
    decode_json,
    extract_channels,
    gzip_bytes,
    serialise_xml,
    write_outputs,
    xmltv_time,
)
from src.validate_release import validate_assets


class GeneratorTests(unittest.TestCase):
    def test_extracts_tvg_id_before_legacy_channel_metadata(self):
        playlist = {
            "channels": [
                {"tvg-id": "TVE", "CodCadenaTv": "legacy", "Nombre": "LA 1", "Logo": "https://logo"},
                {"CodCadenaTv": "A3", "Nombre": "Antena 3"},
                {"tvg-id": "TVE", "Nombre": "ignored"},
                {"Nombre": "missing"},
            ]
        }
        self.assertEqual(extract_channels(playlist), [Channel("A3", "Antena 3"), Channel("TVE", "LA 1", "https://logo")])

    def test_extracts_legacy_channel_metadata_when_tvg_id_is_absent(self):
        playlist = {"channels": [{"CodCadenaTv": "TVE", "Nombre": "LA 1", "Logo": "https://logo"}]}
        self.assertEqual(extract_channels(playlist), [Channel("TVE", "LA 1", "https://logo")])

    def test_invalid_playlist_is_rejected(self):
        with self.assertRaises(ValueError):
            extract_channels({"channels": []})

    def test_timestamp_is_utc_xmltv(self):
        with self.assertRaises(ValueError):
            xmltv_time(0)
        self.assertEqual(xmltv_time(1790467200000), "20260927000000 +0000")

    def test_feed_escapes_content_and_uses_previous_on_failure(self):
        channels = [Channel("TVE", "LA & 1"), Channel("A3", "Antena 3")]
        old_channel = ET.Element("channel", {"id": "A3"})
        ET.SubElement(old_channel, "display-name").text = "Antena 3"
        old_programme = ET.Element("programme", {"channel": "A3", "start": "20260927000000 +0000", "stop": "20260927010000 +0000"})
        ET.SubElement(old_programme, "title").text = "Old"

        def fetcher(channel):
            if channel.code == "TVE":
                return [{"FechaHoraInicio": 1790467200000, "FechaHoraFin": 1790470800000, "Titulo": "A < B & C"}]
            raise RuntimeError("source unavailable")

        root, status = build_feed(channels, fetcher, {"A3": [old_channel, old_programme]})
        xml = serialise_xml(root)
        self.assertIn(b"A &lt; B &amp; C", xml)
        self.assertEqual(status["published_channels"], 2)
        self.assertEqual(status["stale_channels"], 1)
        self.assertEqual(root.find("channel").get("id"), "TVE")

    def test_empty_fresh_data_fails(self):
        with self.assertRaises(RuntimeError):
            build_feed([Channel("TVE", "LA 1")], lambda _: [])

    def test_compact_feed_keeps_current_programmes_and_degrades_to_twelve_hours(self):
        now = datetime(2026, 9, 29, 21, 0, tzinfo=timezone.utc)
        root = ET.Element("tv")
        channel = ET.SubElement(root, "channel", {"id": "TVE"})
        ET.SubElement(channel, "display-name").text = "LA 1"
        for start, stop, title in (
            (now - timedelta(minutes=5), now + timedelta(minutes=25), "Current"),
            (now + timedelta(hours=20), now + timedelta(hours=21), "Later"),
        ):
            programme = ET.SubElement(
                root,
                "programme",
                {
                    "channel": "TVE",
                    "start": start.strftime("%Y%m%d%H%M%S +0000"),
                    "stop": stop.strftime("%Y%m%d%H%M%S +0000"),
                },
            )
            ET.SubElement(programme, "title").text = title
            ET.SubElement(programme, "desc").text = "must not be published in compact XMLTV"

        twelve_xml, _ = compact_xml(root, now, 12)
        compact, hours, programmes = build_compact_xml(
            root,
            now,
            max_bytes=len(gzip_bytes(twelve_xml)),
            windows=(24, 12),
        )
        self.assertEqual(hours, 12)
        self.assertEqual(programmes, 1)
        self.assertIn(b"Current", compact)
        self.assertNotIn(b"Later", compact)
        self.assertNotIn(b"must not be published", compact)

    def test_writes_valid_gzip_status_and_release_assets(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            root = ET.Element("tv")
            channel = ET.SubElement(root, "channel", {"id": "TVE"})
            ET.SubElement(channel, "display-name").text = "LA 1"
            programme = ET.SubElement(
                root,
                "programme",
                {"channel": "TVE", "start": "20260929210000 +0000", "stop": "20260929220000 +0000"},
            )
            ET.SubElement(programme, "title").text = "Test"
            xml = serialise_xml(root)
            compact, hours, programmes = build_compact_xml(root, datetime(2026, 9, 29, 21, tzinfo=timezone.utc))
            write_outputs(output, xml, compact, {"published_channels": 1, "failed_channels": 0}, hours, programmes)
            self.assertEqual(gzip.decompress((output / "movistar.xml.gz").read_bytes()), xml)
            self.assertLessEqual((output / "movistar-compact.xml.gz").stat().st_size, 40 * 1024)
            self.assertEqual(json.loads((output / "status.json").read_text())["published_channels"], 1)
            self.assertEqual(validate_assets(output, {"TVE"})["full_programmes"], 1)


if __name__ == "__main__":
    unittest.main()
