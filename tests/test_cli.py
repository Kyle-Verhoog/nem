import logging
import os
import pathlib
import subprocess
import shutil
import unittest

from nem.log import get_logger


log = get_logger(__name__)
CWD = os.path.dirname(os.path.realpath(__file__))


def Any(cls, can_be_falsy=False):
    class Any(cls):
        def __eq__(self, other):
            if not can_be_falsy and not other:
                return False
            if isinstance(other, cls):
                return True
            return False
    return Any()


def assert_nem(args, *expecteds):
    pipe = subprocess.Popen(['n', *args], stdout=subprocess.PIPE, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    for inp, out, err in expecteds:
        o, e = pipe.communicate(input=inp.encode('utf-8'))

        assert out == o, f'stdout does not match: "{out}" does not equal "{o}"'
        assert err == e, f'stderr does not match: "{err}" does not equal "{e}"'


def assert_file_exists(f):
    assert os.path.exists(f) and os.path.isfile(f)


def safe_rm_file(f):
    p = pathlib.Path(f)
    while os.path.exists(p) and os.path.isfile(p):
        parent = p.parent
        backup = parent / f'{str(p)}.bak'
        try:
            os.rename(p, backup)
        except OSError:
            pass
        p = backup


@unittest.skipIf(os.environ.get('CI') is None, 'not in CI')
@unittest.skipIf(not shutil.which('n'), 'nem is not installed')
class TestCLI(unittest.TestCase):

    def setUp(self):
        pass

    def tearDown(self):
        pass

    def test_sanity(self):
        assert_nem(
            '',
            ('y', Any(bytes), b''),
        )

    def test_non_existent_command(self):
        assert_nem(
            '/#@!#$#',
            ('y', b'', Any(bytes)),
        )
