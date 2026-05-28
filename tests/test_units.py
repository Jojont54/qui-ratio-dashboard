import unittest

from qui_ratio_dashboard.units import has_byte_unit, parse_bytes


class UnitParsingTests(unittest.TestCase):
    def test_french_and_iec_units_are_case_insensitive_binary_units(self):
        self.assertEqual(parse_bytes("1 TiB"), 1024**4)
        self.assertEqual(parse_bytes("1 tio"), 1024**4)
        self.assertEqual(parse_bytes("1 TO"), 1024**4)
        self.assertEqual(parse_bytes("1 TB"), 1024**4)
        self.assertEqual(parse_bytes("1 t"), 1024**4)
        self.assertEqual(parse_bytes("1 GiB"), 1024**3)
        self.assertEqual(parse_bytes("1 gio"), 1024**3)
        self.assertEqual(parse_bytes("1 Go"), 1024**3)
        self.assertEqual(parse_bytes("1 GB"), 1024**3)
        self.assertEqual(parse_bytes("1 G"), 1024**3)
        self.assertEqual(parse_bytes("1 MiB"), 1024**2)
        self.assertEqual(parse_bytes("1 mio"), 1024**2)
        self.assertEqual(parse_bytes("1 Mo"), 1024**2)
        self.assertEqual(parse_bytes("1 MB"), 1024**2)
        self.assertEqual(parse_bytes("1 m"), 1024**2)
        self.assertEqual(parse_bytes("1 KiB"), 1024)
        self.assertEqual(parse_bytes("1 Ko"), 1024)
        self.assertEqual(parse_bytes("1 KB"), 1024)
        self.assertEqual(parse_bytes("1 K"), 1024)

    def test_decimal_commas_from_tracker_sites_are_supported(self):
        self.assertEqual(parse_bytes("1,5 To"), int(1.5 * 1024**4))
        self.assertEqual(parse_bytes("2,25 Gio"), int(2.25 * 1024**3))

    def test_missing_unit_can_be_detected_for_user_input(self):
        self.assertFalse(has_byte_unit("10"))
        self.assertFalse(has_byte_unit("10,5"))
        self.assertFalse(has_byte_unit(""))
        self.assertTrue(has_byte_unit("10 Go"))
        self.assertTrue(has_byte_unit("10 G"))


if __name__ == "__main__":
    unittest.main()
