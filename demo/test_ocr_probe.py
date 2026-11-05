"""Local OCR probe contract on synthetic images; never runs on user frames."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


HERE = Path(__file__).resolve().parent
FONT = Path('/System/Library/Fonts/STHeiti Medium.ttc')
SWIFT = Path('/usr/bin/swift')


@unittest.skipUnless(sys.platform == 'darwin' and FONT.exists() and SWIFT.exists(),
                     'macOS Vision, Swift and the test font are required')
class OCRProbeTests(unittest.TestCase):
    def test_exact_labels_coordinates_duplicates_and_orientation(self):
        font = ImageFont.truetype(str(FONT), 13)
        cases = (
            ('mixed', (390, 844), ((30, '设置'), (121, '微信'), (216, '地图')), 1),
            ('duplicate', (390, 844), ((60, '微信'), (220, '微信')), 1),
            ('absent', (390, 844), ((30, '设置'), (216, '地图')), 1),
            ('scaled', (384, 832), ((30, '设置'), (120, '微信')), 1),
            ('rotated', (390, 844), ((121, '微信'),), 6),
        )
        with tempfile.TemporaryDirectory(prefix='phonepilot-ocr-test-') as directory:
            for name, size, labels, orientation in cases:
                with self.subTest(name=name):
                    image = Image.new('RGB', size, (57, 78, 112))
                    draw = ImageDraw.Draw(image)
                    expected = []
                    for x, label in labels:
                        draw.rounded_rectangle((x, 220, x+54, 274), radius=12, fill=(30, 150, 70))
                        draw.text((x+13, 279), label, font=font, fill='white')
                        if label == '微信': expected.append(draw.textbbox((x+13, 279), label, font=font))
                    path = Path(directory) / (name+'.jpg')
                    exif = Image.Exif(); exif[274] = orientation
                    if orientation == 6: image = image.transpose(Image.Transpose.ROTATE_90)
                    image.save(path, format='JPEG', quality=80, exif=exif)
                    result = subprocess.run([str(SWIFT), str(HERE/'ocr_probe.swift'), str(path), '微信'],
                                            capture_output=True, text=True, check=True, timeout=60)
                    data = json.loads(result.stdout)
                    self.assertEqual((data['width_px'], data['height_px']), size)
                    self.assertEqual(len(data['matches']), len(expected))
                    for match in data['matches']:
                        self.assertEqual(set(match), {'box_px', 'confidence'})
                        left, top, right, bottom = match['box_px']
                        x, y = (left+right)/2, (top+bottom)/2
                        self.assertTrue(any(x0-5 <= x <= x1+5 and y0-5 <= y <= y1+5
                                            for x0, y0, x1, y1 in expected))


if __name__ == '__main__':
    unittest.main()
