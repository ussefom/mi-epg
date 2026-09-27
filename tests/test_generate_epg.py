import gzip
import json
import unittest
import xml.etree.ElementTree as ET

from src.generate_epg import Channel, build_feed, decode_json, extract_channels, serialise_xml, write_outputs, xmltv_time


class GeneratorTests(unittest.TestCase):
    def test_extracts_only_safe_distinct_channel_metadata(self):
        playlist = {"channels": [{"CodCadenaTv": "TVE", "Nombre": "LA 1", "Logo": "https://logo"}, {"CodCadenaTv": "TVE", "Nombre": "ignored"}, {"Nombre": "missing"}]}
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

    def test_writes_valid_gzip_and_safe_status(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            write_outputs(output, b"<tv/>", {"published_channels": 1, "failed_channels": 0})
            self.assertEqual(gzip.decompress((output / "movistar.xml.gz").read_bytes()), b"<tv/>")
            self.assertEqual(json.loads((output / "status.json").read_text())["published_channels"], 1)


if __name__ == "__main__":
    unittest.main()
