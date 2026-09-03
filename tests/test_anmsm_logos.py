import io
import unittest
from unittest.mock import patch

from PIL import Image

from app.services.anmsm_logos import LogoImportError, _assert_public_https, optimize, parse_station


class AnmsmLogoProcessingTests(unittest.TestCase):
    def png(self, size, box):
        image = Image.new("RGBA", size, (0, 0, 0, 0))
        image.paste((255, 0, 0, 255), box)
        output = io.BytesIO(); image.save(output, "PNG")
        return output.getvalue()

    def test_transparent_margins_are_removed_without_distortion(self):
        encoded, data = optimize(self.png((400, 200), (100, 50, 300, 150)))
        result = Image.open(io.BytesIO(encoded))
        self.assertEqual(result.size, (512, 512))
        self.assertEqual((data["content_width"], data["content_height"]), (200, 100))
        self.assertEqual(data["aspect_ratio"], 2)
        self.assertEqual((data["visual_occupancy_width"], data["visual_occupancy_height"]), (200/512, 100/512))
        self.assertLessEqual(len(encoded), 50 * 1024)

    def test_small_sources_are_not_upscaled(self):
        _, data = optimize(self.png((50, 25), (0, 0, 50, 25)))
        self.assertEqual(data["visual_occupancy_width"], 50/512)
        self.assertIn("low_resolution", data["warnings"])

    @patch("app.services.anmsm_logos.socket.getaddrinfo", return_value=[(None, None, None, None, ("127.0.0.1", 443))])
    def test_private_dns_result_is_rejected(self, _resolver):
        with self.assertRaises(LogoImportError) as raised:
            _assert_public_https("https://anmsm.media.tourinsoft.eu/logo.png")
        self.assertEqual(raised.exception.code, "ssrf_blocked")

    def test_non_allowlisted_host_is_rejected_before_dns(self):
        with self.assertRaises(LogoImportError):
            _assert_public_https("https://example.org/logo.png")

    def test_shared_parser_uses_real_tourinsoft_fields(self):
        station = parse_station({"SyndicObjectID": " 123 ", "SyndicObjectName": "Station réelle",
            "LOGO": [{"Url": "https://logo", "Titre": "Titre", "Credit": "Crédit"}]})
        self.assertEqual(station["external_station_id"], "123")
        self.assertEqual(station["external_name"], "Station réelle")
        self.assertEqual(station["logo"]["title"], "Titre")

    def test_shared_parser_prefers_real_anmsm_station_name(self):
        station = parse_station({"SyndicObjectID": "42",
                                 "SyndicObjectName": "Libellé technique",
                                 "Object": {"NOM": "Nom ANMSM réel", "LOGO": [{
                                     "Url": "https://anmsm.media.tourinsoft.eu/logo.png",
                                     "MediaID": "media-42", "Titre": "Logo officiel",
                                     "Credit": "ANMSM"}]}})
        self.assertEqual(station["external_name"], "Nom ANMSM réel")
        self.assertEqual(station["logo"], {
            "url": "https://anmsm.media.tourinsoft.eu/logo.png",
            "media_id": "media-42", "title": "Logo officiel", "credit": "ANMSM"})


if __name__ == "__main__":
    unittest.main()
