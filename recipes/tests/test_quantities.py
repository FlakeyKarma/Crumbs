from decimal import Decimal

from django.test import SimpleTestCase

from recipes.quantities import format_duration, format_quantity


class FormatQuantityTests(SimpleTestCase):
    def test_none_is_blank(self):
        self.assertEqual(format_quantity(None), "")

    def test_whole_numbers_lose_their_decimals(self):
        self.assertEqual(format_quantity(Decimal("3.000")), "3")

    def test_common_fractions_become_single_characters(self):
        self.assertEqual(format_quantity(Decimal("0.5")), "\u00bd")
        self.assertEqual(format_quantity(Decimal("0.25")), "\u00bc")
        self.assertEqual(format_quantity(Decimal("0.75")), "\u00be")

    def test_mixed_numbers(self):
        self.assertEqual(format_quantity(Decimal("1.5")), "1\u00bd")
        self.assertEqual(format_quantity(Decimal("2.25")), "2\u00bc")

    def test_thirds_survive_the_rounding(self):
        self.assertEqual(format_quantity(Decimal("0.333")), "\u2153")
        self.assertEqual(format_quantity(Decimal("0.667")), "\u2154")

    def test_large_amounts_stay_decimal(self):
        self.assertEqual(format_quantity(Decimal("250")), "250")
        self.assertEqual(format_quantity(Decimal("1.6")), "1\u2157")
        self.assertEqual(format_quantity(Decimal("12.5")), "12.5")

    def test_awkward_small_amounts_fall_back_to_decimals(self):
        self.assertEqual(format_quantity(Decimal("0.07")), "0.07")

    def test_rounding_dust_reads_as_zero(self):
        self.assertEqual(format_quantity(Decimal("0.0001")), "0")


class FormatDurationTests(SimpleTestCase):
    def test_minutes(self):
        self.assertEqual(format_duration(45), "45 min")

    def test_whole_hours(self):
        self.assertEqual(format_duration(120), "2 hr")

    def test_hours_and_minutes(self):
        self.assertEqual(format_duration(90), "1 hr 30 min")

    def test_nothing(self):
        self.assertEqual(format_duration(None), "")
        self.assertEqual(format_duration(0), "")
