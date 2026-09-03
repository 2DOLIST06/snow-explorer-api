"""Regression tests for failures that must never escape the image child process."""
import io
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from app.services.anmsm_image_worker import convert
from app.services.anmsm_logos import LogoImportError, _convert_subprocess, download


class _Response:
    status_code = 200
    headers = {"Content-Type": "image/png"}

    def __init__(self, chunks): self.chunks, self.closed = chunks, False
    def iter_content(self, _size): return iter(self.chunks)
    def close(self): self.closed = True


class _Session:
    def __init__(self, response): self.response = response
    def get(self, *_args, **_kwargs): return self.response


class AnmsmLogoSafetyTests(unittest.TestCase):
    def image(self, image_format="PNG", size=(64, 32)):
        output = io.BytesIO()
        Image.new("RGBA", size, "red").save(output, image_format)
        return output.getvalue()

    def convert_bytes(self, raw, max_pixels=40_000_000):
        with tempfile.TemporaryDirectory() as directory:
            source, output = os.path.join(directory, "source"), os.path.join(directory, "out.webp")
            with open(source, "wb") as stream: stream.write(raw)
            metadata = convert(source, output, max_pixels, 512, 50 * 1024)
            self.assertLessEqual(os.path.getsize(output), 50 * 1024)
            return metadata

    def test_normal_image_is_bounded_and_keeps_proportions(self):
        metadata = self.convert_bytes(self.image(size=(320, 160)))
        self.assertEqual(metadata["optimized_width"], 512)
        self.assertEqual(metadata["aspect_ratio"], 2)

    def test_extremely_compressed_excessive_dimensions_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "excessive_dimensions"):
            self.convert_bytes(self.image(size=(2000, 2000)), max_pixels=1000)

    def test_corrupt_and_unsupported_files_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "invalid_image"):
            self.convert_bytes(b"not an image")
        with self.assertRaisesRegex(ValueError, "unsupported_format"):
            self.convert_bytes(self.image("BMP"))

    @patch("app.services.anmsm_logos._assert_public_https")
    def test_stream_limit_closes_response_and_removes_temporary_file(self, _public):
        response = _Response([b"1234", b"5678"])
        with patch.dict(os.environ, {"ANMSM_LOGO_MAX_DOWNLOAD_BYTES": "5"}), \
             patch("app.services.anmsm_logos.os.unlink", wraps=os.unlink) as unlink:
            with self.assertRaisesRegex(LogoImportError, "size limit"):
                download("https://anmsm.media.tourinsoft.eu/a.png", _Session(response))
        self.assertTrue(response.closed); unlink.assert_called_once()
        self.assertFalse(os.path.exists(unlink.call_args.args[0]))

    def test_child_timeout_and_signal_become_controlled_errors(self):
        with patch("app.services.anmsm_logos.subprocess.run",
                   side_effect=subprocess.TimeoutExpired("worker", 1)):
            with self.assertRaises(LogoImportError) as timeout:
                _convert_subprocess("source", "output")
        self.assertEqual(timeout.exception.code, "conversion_timeout")
        result = subprocess.CompletedProcess([], -9, "", "")
        with patch("app.services.anmsm_logos.subprocess.run", return_value=result):
            with self.assertRaises(LogoImportError) as signal:
                _convert_subprocess("source", "output")
        self.assertEqual(signal.exception.code, "conversion_interrupted")

    def test_success_still_works_immediately_after_invalid_image(self):
        with self.assertRaisesRegex(ValueError, "invalid_image"):
            self.convert_bytes(b"corrupt")
        self.assertEqual(self.convert_bytes(self.image())["source_format"], "png")


if __name__ == "__main__":
    unittest.main()
