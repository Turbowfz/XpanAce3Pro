"""Local decoded-gain replay tests; no HEIC encoder, production hooks or phone."""
import contextlib
import hashlib
import importlib.util
import io
from pathlib import Path
import tempfile
import types
import unittest

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/replay_gain_frame.py"
spec = importlib.util.spec_from_file_location("gain_frame_replay", SCRIPT)
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)


class DecodedReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        parent = ROOT / "_tmp"
        parent.mkdir(exist_ok=True)
        cls.output = Path(tempfile.mkdtemp(prefix="gain-frame-replay-tests-", dir=parent))
        cls.inputs = cls.output / "input"
        cls.inputs.mkdir()

    def image_file(self, name, pixels):
        path = self.inputs / name
        Image.fromarray(pixels).save(path)
        return path

    def test_geometry_rejects_empty_outside_and_overbudget(self):
        replay.geometry(5, 4, (1, 1, 4, 3), 3)
        for width, height, roi, bpp in ((0, 4, (0, 0, 1, 1), 1), (16385, 4, (0, 0, 1, 1), 1),
                (5, 4, (-1, 0, 3, 3), 1), (5, 4, (1, 1, 1, 3), 1), (5, 4, (0, 0, 6, 4), 3),
                (16384, 16384, (0, 0, 1, 1), 3)):
            with self.subTest(width=width, height=height, roi=roi), self.assertRaises(ValueError):
                replay.geometry(width, height, roi, bpp)

    def test_y8_lossless_channels_only_no_luminance_guess(self):
        channel = np.arange(20, dtype=np.uint8).reshape(4, 5)
        equal = self.image_file("equal.png", np.repeat(channel[:, :, None], 3, axis=2))
        _, digest, result, replicated = replay.decoded_input(equal, "y8", (1, 1, 4, 3))
        self.assertTrue(replicated)
        self.assertTrue(np.array_equal(result, channel))
        self.assertEqual(hashlib.sha256(equal.read_bytes()).hexdigest(), digest)
        unequal = np.repeat(channel[:, :, None], 3, axis=2)
        unequal[2, 2, 1] += 1
        path = self.image_file("unequal.png", unequal)
        with self.assertRaises(ValueError):
            replay.decoded_input(path, "y8", (1, 1, 4, 3))

    def test_unsupported_channel_mode_and_format_rejected(self):
        gray = self.image_file("gray.png", np.zeros((4, 5), dtype=np.uint8))
        with self.assertRaises(ValueError):
            replay.decoded_input(gray, "rgb888", (1, 1, 4, 3))
        rgba = self.image_file("alpha.png", np.zeros((4, 5, 4), dtype=np.uint8))
        with self.assertRaises(ValueError):
            replay.decoded_input(rgba, "y8", (1, 1, 4, 3))
        jpeg = self.image_file("gain.jpg", np.zeros((4, 5, 3), dtype=np.uint8))
        with self.assertRaises(ValueError):
            replay.decoded_input(jpeg, "rgb888", (1, 1, 4, 3))

    def test_compiled_core_replay_preserves_each_format_and_source(self):
        roi = (1, 1, 4, 3)
        for format_name, shape in (("y8", (4, 5)), ("rgb888", (4, 5, 3))):
            with self.subTest(format=format_name):
                data = np.arange(np.prod(shape), dtype=np.uint8).reshape(shape)
                data[1, 1] = 0
                path = self.image_file("compiled-" + format_name + ".png", data)
                original = path.read_bytes()
                args = types.SimpleNamespace(gain_png=path, format=format_name, roi=roi,
                    source_heic=None, host_cxx=None, sanitizer="none")
                with contextlib.redirect_stdout(io.StringIO()):
                    output, report = replay.replay(args)
                self.assertTrue(report["ok"])
                self.assertFalse(report["in_apk"])
                self.assertFalse(report["heic_reencoded"])
                self.assertEqual(output.parent, (ROOT / "_tmp").resolve())
                self.assertEqual(path.read_bytes(), original)
                self.assertEqual(report["pixels"]["changed_photo_pixels"], 0)
                self.assertTrue(report["pixels"]["all_frame_bytes_zero"])
                with Image.open(report["result"]["png"]) as image:
                    result = np.array(image)
                expected = np.zeros_like(data)
                expected[1:3, 1:4] = data[1:3, 1:4]
                self.assertTrue(np.array_equal(result, expected))
                self.assertFalse(list(output.glob("*.heic")))
                self.assertFalse(list(output.glob("*.apk")))
                self.assertFalse(list(output.glob("*.zip")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
