import json
import os
import random
import subprocess
import sys
import tempfile
import unittest

from PIL import Image


class CoverPhotoWorkerTests(unittest.TestCase):
    def run_worker(self, source, directory, adaptive):
        command = [sys.executable, "-m", "app.services.anmsm_piste_map_worker", source,
                   "--output-directory", directory, "--max-pixels", "24000000",
                   "--max-dimension", "1600", "--output-limit", "81920",
                   "--quality", "88", "--memory-mb", "512"]
        if adaptive:
            command.append("--adaptive-photo")
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        return result.returncode, json.loads(result.stdout)

    def test_detailed_photo_fits_budget_by_resizing_without_changing_map_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "detailed.png")
            image = Image.frombytes("RGB", (1600, 1000), random.Random(7).randbytes(1600 * 1000 * 3))
            image.save(source)
            image.close()
            # Map mode retains its dimensions/readability policy and refuses a
            # file that cannot fit, rather than silently shrinking its labels.
            code, result = self.run_worker(source, directory, adaptive=False)
            self.assertEqual(code, 2)
            self.assertEqual(result["error"], "optimization_limit")
            code, result = self.run_worker(source, directory, adaptive=True)
            self.assertEqual(code, 0, result)
            self.assertGreater(result["size"], 0)
            self.assertLessEqual(result["size"], 80 * 1024)
            self.assertLess(result["width"], 1600)
            self.assertAlmostEqual(result["width"] / result["height"], 1.6, delta=.02)
            with Image.open(result["path"]) as converted:
                self.assertEqual(converted.format, "WEBP")
                self.assertEqual(converted.size, (result["width"], result["height"]))

    def test_photo_already_under_budget_keeps_its_dimensions(self):
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "simple.png")
            with Image.new("RGB", (640, 360), "skyblue") as image:
                image.save(source)
            code, result = self.run_worker(source, directory, adaptive=True)
            self.assertEqual(code, 0, result)
            self.assertEqual((result["width"], result["height"]), (640, 360))
            self.assertLessEqual(result["size"], 80 * 1024)
