"""Adapter availability tests (requires Home Assistant)."""

from types import SimpleNamespace
from unittest import TestCase

from custom_components.kumo_cloud.climate import KumoCloudClimate
from custom_components.kumo_cloud.coordinator import KumoCloudDevice


class LegacyAvailabilityTests(TestCase):
    def device(self, *, adapter, details, success=True):
        coordinator = SimpleNamespace(
            zones=[{"id": "dummy-zone", "adapter": adapter}],
            devices={"dummy-device": details}, device_profiles={},
            data={}, last_update_success=success,
        )
        return KumoCloudDevice(coordinator, "dummy-zone", "dummy-device")

    def test_legacy_adapter_with_fresh_details_is_available(self):
        for mode, power in (("cool", 1), ("off", 0)):
            with self.subTest(mode=mode):
                device = self.device(
                    adapter={"isIoT": False, "connected": False},
                    details={"connected": False, "operationMode": mode, "power": power},
                )
                self.assertTrue(device.available)
                entity = KumoCloudClimate(device)
                self.assertTrue(entity.available)
                self.assertEqual(entity.hvac_mode, mode)

    def test_failed_poll_is_unavailable(self):
        device = self.device(
            adapter={"isIoT": False, "connected": False},
            details={"power": 0}, success=False,
        )
        self.assertFalse(device.available)

    def test_missing_details_is_unavailable(self):
        device = self.device(adapter={"isIoT": False}, details={})
        self.assertFalse(device.available)

    def test_iot_and_unknown_adapters_keep_connection_check(self):
        for adapter in ({"isIoT": True, "connected": False}, {"connected": False}):
            with self.subTest(adapter=adapter):
                self.assertFalse(self.device(adapter=adapter, details={"power": 0}).available)
                self.assertTrue(self.device(adapter=adapter, details={"connected": True}).available)
