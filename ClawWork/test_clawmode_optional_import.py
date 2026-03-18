import unittest


class ClawModeOptionalImportTests(unittest.TestCase):
    def test_package_import_is_lazy(self):
        import clawmode_integration

        self.assertIn("ClawWorkAgentLoop", clawmode_integration.__all__)


if __name__ == "__main__":
    unittest.main()
