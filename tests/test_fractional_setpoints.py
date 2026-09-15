"""Fractional setpoint conversion tests (requires Home Assistant)."""

from unittest import TestCase

from custom_components.kumo_cloud.climate import _F_TO_C, _f_to_c


class FractionalSetpointTests(TestCase):
    def test_fractional_values_round_in_celsius(self):
        for fahrenheit, celsius in ((68.9, 20.5), (69.8, 21.0), (70.2, 21.0)):
            with self.subTest(fahrenheit=fahrenheit):
                self.assertEqual(_f_to_c(fahrenheit), celsius)

    def test_integer_values_keep_mitsubishi_mapping(self):
        for fahrenheit, celsius in _F_TO_C.items():
            with self.subTest(fahrenheit=fahrenheit):
                self.assertEqual(_f_to_c(fahrenheit), celsius)
                self.assertEqual(_f_to_c(float(fahrenheit)), celsius)

    def test_missing_and_out_of_table_values(self):
        self.assertIsNone(_f_to_c(None))
        self.assertEqual(_f_to_c(86), 30.0)
