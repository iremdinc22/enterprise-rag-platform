"""Explicit opt-in runner; ordinary unittest discovery never starts live E2E."""
from tests.e2e.bootstrap import install

install()

import sys
import unittest

result = unittest.TextTestRunner(verbosity=2).run(
    unittest.defaultTestLoader.loadTestsFromName("tests.e2e.suite")
)
sys.exit(not result.wasSuccessful())
