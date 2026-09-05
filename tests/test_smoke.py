"""テスト基盤が動くことだけを確かめる。"""

import os
import unittest

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


class SmokeTest(unittest.TestCase):
    def test_fixtures_exist(self):
        self.assertTrue(os.path.isdir(os.path.join(FIXTURES, "next-prisma")))
        self.assertTrue(os.path.isdir(os.path.join(FIXTURES, "strapi")))
