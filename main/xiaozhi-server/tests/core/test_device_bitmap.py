"""Check the installed monochrome icon catalog and 1-bit title renderer."""

import unittest

from PIL import Image

from plugins_func.functions.device_bitmap import ICON_CATALOG, render_icon_title


class DeviceBitmapTest(unittest.TestCase):
    def test_catalog_icons_render_as_distinct_one_bit_frames(self):
        frames = {}
        for name in ICON_CATALOG:
            data = render_icon_title(name, "图")
            self.assertEqual(len(data), 2400, name)
            image = Image.frombytes("1", (160, 120), data)
            self.assertEqual(image.getextrema(), (0, 255), name)
            frames[name] = data
        self.assertGreaterEqual(len(frames), 90)
        self.assertNotEqual(frames["cat"], frames["dog"])

    def test_chinese_title_changes_frame_and_unknown_icon_is_rejected(self):
        self.assertNotEqual(render_icon_title("cat", "小猫"),
                            render_icon_title("cat", "大猫"))
        with self.assertRaises(ValueError):
            render_icon_title("not-in-catalog", "小猫")

    def test_nerd_font_and_local_sf_title_are_optional(self):
        emoji = render_icon_title("cat", "Cat")
        nerd = render_icon_title("md-cat", "Cat", source="nerd", title_font="sf")
        self.assertEqual(len(nerd), 2400)
        self.assertNotEqual(emoji, nerd)
        self.assertEqual(Image.frombytes("1", (160, 120), nerd).getextrema(), (0, 255))
        with self.assertRaises(ValueError):
            render_icon_title("md-not-a-real-icon", "Cat", source="nerd")


if __name__ == "__main__":
    unittest.main()
