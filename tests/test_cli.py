import logging
import os
import subprocess
import shutil
import unittest

from nem.log import get_logger


log = get_logger(__name__)
CWD = os.path.dirname(os.path.realpath(__file__))


def _nem(*args):
    res = subprocess.run([f'n', *args], capture_output=True)
    return res


def assert_file_exists(f):
    assert os.path.exists(f) and os.path.isfile(f)


@unittest.skipIf(os.environ.get('CI') is None, 'not in CI')
@unittest.skipIf(not shutil.which('n'), 'nem is not installed')
class TestCLI(unittest.TestCase):

    def setUp(self):
        pass

    def tearDown(self):
        pass

    def test_sanity(self):
        res = _nem()
        self.assertNotEqual(res.stdout, b'')
        self.assertEqual(res.stderr, b'')

    def test_non_existent_command(self):
        res = _nem('/#@#$@#@$')
        self.assertEqual(res.stdout, b'')
        self.assertNotEqual(res.stderr, b'')
