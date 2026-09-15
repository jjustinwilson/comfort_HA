"""Climate humidity mapping tests (requires Home Assistant)."""

from types import SimpleNamespace
from unittest import TestCase

from custom_components.kumo_cloud.climate import KumoCloudClimate


class CurrentHumidityTests(TestCase):
    def test_device_reading_and_adapter_fallback(self):
        for adapter, details, expected in (
            ({"humidity": 36.96875}, {}, 36.96875),
            ({"humidity": 36.96875}, {"humidity": 41}, 41),
            ({"humidity": 36.96875}, {"humidity": 0}, 0),
            ({}, {}, None),
            ({"humidity": 36.96875}, {"humidity": None}, None),
        ):
            with self.subTest(adapter=adapter, details=details):
                device = SimpleNamespace(
                    coordinator=SimpleNamespace(), unique_id="dummy",
                    profile_data=[], zone_data={"adapter": adapter},
                    device_data=details,
                )
                entity = KumoCloudClimate(device)
                self.assertEqual(entity.current_humidity, expected)
