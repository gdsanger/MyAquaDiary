"""Die Konventionen der Eheim-API — genau hier entstehen sonst Fehler, die
erst am Gerät auffallen."""

import datetime
from decimal import Decimal

from django.test import SimpleTestCase

from services.eheim import convert


class MinutesSinceMidnightTests(SimpleTestCase):
    def test_time_becomes_minutes(self):
        self.assertEqual(convert.to_minutes(datetime.time(15, 0)), 900)
        self.assertEqual(convert.to_minutes(datetime.time(0, 0)), 0)
        self.assertEqual(convert.to_minutes(datetime.time(23, 59)), 1439)

    def test_day_phase_of_the_test_tank(self):
        # 11:00–23:00 des Testbeckens
        self.assertEqual(convert.to_minutes("11:00"), 660)
        self.assertEqual(convert.to_minutes("23:00"), 1380)

    def test_accepts_string_datetime_and_int(self):
        self.assertEqual(convert.to_minutes("15:00:30"), 900)
        self.assertEqual(convert.to_minutes(datetime.datetime(2026, 9, 6, 15, 0)), 900)
        self.assertEqual(convert.to_minutes(900), 900)
        self.assertEqual(convert.to_minutes("900"), 900)

    def test_round_trip(self):
        self.assertEqual(convert.from_minutes(900), datetime.time(15, 0))
        self.assertEqual(convert.to_minutes(convert.from_minutes(660)), 660)

    def test_format_for_display(self):
        self.assertEqual(convert.format_minutes(900), "15:00")
        self.assertEqual(convert.format_minutes(0), "00:00")

    def test_values_outside_the_day_are_rejected(self):
        for value in (-1, 1440, "24:00", "25:30"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    convert.to_minutes(value)

    def test_unreadable_values_are_rejected(self):
        for value in (None, "viertel nach drei", True):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    convert.to_minutes(value)

    def test_parse_is_tolerant(self):
        self.assertEqual(convert.parse_minutes(900), 900)
        self.assertIsNone(convert.parse_minutes(None))
        self.assertIsNone(convert.parse_minutes(9999))


class TenthsTests(SimpleTestCase):
    def test_temperature_becomes_tenths(self):
        self.assertEqual(convert.to_tenths(Decimal("23.5")), 235)
        self.assertEqual(convert.to_tenths(23.5), 235)
        self.assertEqual(convert.to_tenths("23,5"), 235)
        self.assertEqual(convert.to_tenths(24), 240)

    def test_tenths_become_decimal(self):
        self.assertEqual(convert.from_tenths(235), Decimal("23.5"))
        self.assertEqual(convert.from_tenths("235"), Decimal("23.5"))

    def test_rounding_is_half_up(self):
        self.assertEqual(convert.to_tenths(Decimal("23.45")), 235)
        self.assertEqual(convert.to_tenths(Decimal("23.44")), 234)

    def test_round_trip(self):
        self.assertEqual(convert.from_tenths(convert.to_tenths(Decimal("26.8"))), Decimal("26.8"))

    def test_parse_is_tolerant(self):
        self.assertEqual(convert.parse_tenths(235), Decimal("23.5"))
        self.assertIsNone(convert.parse_tenths("kaputt"))
        self.assertIsNone(convert.parse_tenths(None))

    def test_unit_change_converts_all_values(self):
        celsius, fahrenheit = convert.TemperatureUnit.CELSIUS, convert.TemperatureUnit.FAHRENHEIT
        self.assertEqual(convert.convert_temperature(Decimal("23.5"), celsius, fahrenheit), Decimal("74.3"))
        self.assertEqual(convert.convert_temperature(Decimal("74.3"), fahrenheit, celsius), Decimal("23.5"))
        self.assertEqual(convert.convert_temperature(Decimal("23.5"), celsius, celsius), Decimal("23.5"))

    def test_converted_value_survives_the_round_trip_as_tenths(self):
        as_tenths = convert.to_tenths(
            convert.convert_temperature(Decimal("23.5"), 0, convert.TemperatureUnit.FAHRENHEIT)
        )
        self.assertEqual(as_tenths, 743)


class BooleanTests(SimpleTestCase):
    def test_booleans_become_one_and_zero(self):
        self.assertEqual(convert.to_api_bool(True), 1)
        self.assertEqual(convert.to_api_bool(False), 0)
        self.assertEqual(convert.to_api_bool("ja"), 1)
        self.assertEqual(convert.to_api_bool("0"), 0)

    def test_unknown_strings_are_rejected(self):
        with self.assertRaises(ValueError):
            convert.to_api_bool("vielleicht")

    def test_one_and_zero_become_booleans(self):
        self.assertIs(convert.parse_api_bool(1), True)
        self.assertIs(convert.parse_api_bool(0), False)
        self.assertIs(convert.parse_api_bool("true"), True)

    def test_unknown_values_stay_unknown(self):
        self.assertIsNone(convert.parse_api_bool(None))
        self.assertIsNone(convert.parse_api_bool("vielleicht"))


class ParseIntTests(SimpleTestCase):
    def test_reads_numbers_from_device_answers(self):
        self.assertEqual(convert.parse_int(42), 42)
        self.assertEqual(convert.parse_int("42"), 42)
        self.assertEqual(convert.parse_int(41.6), 42)

    def test_unreadable_values_are_none(self):
        self.assertIsNone(convert.parse_int(None))
        self.assertIsNone(convert.parse_int("keine Zahl"))
        self.assertIsNone(convert.parse_int(True))
