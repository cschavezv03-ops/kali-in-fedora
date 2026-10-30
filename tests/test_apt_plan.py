"""APT plans must use simulations and fail closed on incomplete size metadata."""

import unittest

from lib.apt_plan import estimate


class EstimateTests(unittest.TestCase):
    def setUp(self):
        self.simulation = (
            "0 upgraded, 1 newly installed, 0 to remove and 0 not upgraded.\n"
            "Inst example (2.0 kali-rolling [amd64])\n"
            "Conf example (2.0 kali-rolling [amd64])\n"
        )
        self.metadata = "Package: example\nVersion: 2.0\nSize: 1200\nInstalled-Size: 20\n"

    def test_uses_candidate_metadata_for_conservative_budget(self):
        self.assertEqual(estimate(self.simulation, self.metadata), (1200, 20 * 1024))

    def test_rejects_missing_metadata(self):
        with self.assertRaisesRegex(ValueError, "No size metadata"):
            estimate(self.simulation, "")

    def test_rejects_inconsistent_simulation(self):
        with self.assertRaisesRegex(ValueError, "count"):
            estimate("0 upgraded, 2 newly installed, 0 to remove\nInst example (2.0 source [amd64])", self.metadata)

    def test_rejects_removals(self):
        with self.assertRaisesRegex(ValueError, "without removals"):
            estimate(self.simulation.replace("0 to remove", "1 to remove"), self.metadata)


if __name__ == "__main__":
    unittest.main()
