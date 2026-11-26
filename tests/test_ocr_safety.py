import json
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from backend import materials


class OcrSafetyTests(TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(materials, 'ROOT', self.root))
        self.python = self.root / 'python.exe'
        self.python.touch()
        self.enterContext(patch.object(materials, 'ocr_executable', return_value=self.python))
        self.image = self.root / 'image.png'
        self.image.touch()

    def test_retry_does_not_reuse_old_corrected_image(self):
        corrected = self.root / 'previews' / 'doc' / 'image-corrected.png'
        corrected.parent.mkdir(parents=True)
        corrected.write_bytes(b'old image')
        result = SimpleNamespace(returncode=0, stdout=json.dumps({'blocks': [{'text': '去括号', 'bbox': [1, 2, 3, 4]}]}))
        with patch('backend.materials.subprocess.run', return_value=result):
            blocks, previews = materials.process({'id': 'doc', 'path': str(self.image)})
        self.assertFalse(corrected.exists())
        self.assertIsNone(blocks[0]['bbox'])
        self.assertNotIn('原图 · 矫正图', previews)

    def test_timeout_and_malformed_output_are_actionable(self):
        with patch('backend.materials.subprocess.run', side_effect=subprocess.TimeoutExpired('ocr', 300)):
            with self.assertRaisesRegex(RuntimeError, 'OCR 处理超时'):
                materials.process({'id': 'timeout', 'path': str(self.image)})
        with patch('backend.materials.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout='{}')):
            with self.assertRaisesRegex(RuntimeError, 'OCR 返回结果格式无效'):
                materials.process({'id': 'invalid', 'path': str(self.image)})

    def test_full_page_scan_is_not_upscaled_beyond_native_pixels(self):
        scan = SimpleNamespace(get_bounds=lambda: (0, 0, 1400, 800), get_px_size=lambda: (1400, 800))
        page = SimpleNamespace(get_size=lambda: (1400, 800), get_objects=lambda **_: [scan])
        self.assertEqual(materials.page_render_scale(page), 1.0)
        page.get_objects = lambda **_: []
        self.assertEqual(materials.page_render_scale(page), 1.6)
