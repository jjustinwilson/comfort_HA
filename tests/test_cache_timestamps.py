"""Command-cache timestamp regression tests."""

import unittest

from custom_components.kumo_cloud.coordinator import KumoCloudDataUpdateCoordinator


class CacheTimestampTests(unittest.TestCase):
    def setUp(self):
        self.coordinator = object.__new__(KumoCloudDataUpdateCoordinator)
        self.coordinator.devices = {"dummy-device": {}}
        self.coordinator.cached_commands = {
            ("dummy-device", "spCool"): ("2026-01-01T12:00:00+00:00", 21.5),
            ("other-device", "spCool"): ("2026-01-01T12:00:00+00:00", 22.0),
        }

    def test_invalid_cloud_timestamps_preserve_pending_commands(self):
        for timestamp in (None, "", "not-a-date", 42):
            with self.subTest(timestamp=timestamp):
                detail = {"updatedAt": timestamp, "spCool": 20.0}
                self.coordinator._process_pending_commands("dummy-device", detail)
                self.assertEqual(detail["spCool"], 21.5)
                self.assertEqual(len(self.coordinator.cached_commands), 2)

    def test_naive_timestamp_is_interpreted_as_utc(self):
        self.coordinator.cull_cached_commands("dummy-device", "2026-01-01T12:00:01")
        self.assertNotIn(("dummy-device", "spCool"), self.coordinator.cached_commands)
        self.assertIn(("other-device", "spCool"), self.coordinator.cached_commands)

    def test_invalid_cached_timestamp_does_not_abort_other_commands(self):
        self.coordinator.cached_commands[("dummy-device", "power")] = ("invalid", 1)
        self.coordinator.cull_cached_commands("dummy-device", "2026-01-01T12:00:01Z")
        self.assertNotIn(("dummy-device", "spCool"), self.coordinator.cached_commands)
        self.assertIn(("dummy-device", "power"), self.coordinator.cached_commands)

    def test_valid_timestamp_eviction_policy_is_unchanged(self):
        self.coordinator.cull_cached_commands("dummy-device", "2026-01-01T11:59:59Z")
        self.assertEqual(len(self.coordinator.cached_commands), 2)
        self.coordinator.cull_cached_commands("dummy-device", "2026-01-01T13:00:00+01:00")
        self.assertNotIn(("dummy-device", "spCool"), self.coordinator.cached_commands)
        self.assertIn(("other-device", "spCool"), self.coordinator.cached_commands)
