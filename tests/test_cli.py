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


def assert_nem(args, *expecteds, returncode=None):
    pipe = subprocess.Popen(['n', *args], stdout=subprocess.PIPE, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    for inp, out, err in expecteds:
        o, e = pipe.communicate(input=inp.encode('utf-8'))

        assert out == o, f'stdout does not match: "{out}" does not equal "{o}"'
        assert err == e, f'stderr does not match: "{err}" does not equal "{e}"'

    if returncode is not None:
        r = pipe.returncode
        assert returncode == r, f'return code {returncode} does not match {r}'


def file_exists(f):
    return os.path.exists(f) and os.path.isfile(f)


def assert_file_exists(f):
    assert file_exists(f), f'File {f} does not exist or is directory'


def safe_rm_file(f):
    p = pathlib.Path(f)
    while os.path.exists(p) and os.path.isfile(p):
        parent = p.parent
        backup = parent / f'{str(p)}.bak'
        try:
            os.rename(p, backup)
            break
        except OSError:
            pass
        finally:
            p = backup
    return p


def restore_file(f):
    p = f
    p = p[:-4]  # remove .bak suffix
    while p[-4:] == '.bak':
        p = p[:-4]
    os.rename(f, p)



@unittest.skipIf(os.environ.get('CI') is None, 'not in CI')
@unittest.skipIf(not shutil.which('n'), 'nem is not installed')
class TestCLI(unittest.TestCase):

    def setUp(self):
        empty_dbfile = """
version = "0.0.1"

[__table__]
cmds = []
"""
        dbfile = pathlib.Path.home() / '.config' / '.nem.toml'
        if not file_exists(dbfile):
            with open(dbfile, 'w') as f:
                print(empty_dbfile, file=f)


    def tearDown(self):
        pass

    def test_sanity(self):
        # Seems like prompt-toolkit outputs a warning if the output is to the terminal
        assert_nem(
            '',
            ('y', Any(bytes), b'Warning: Output is not to a terminal (fd=1).\n'),
            returncode=0,
        )

    def test_non_existent_command(self):
        assert_nem(
            '/#@!#$#',
            ('y', b'', Any(bytes)),
            returncode=1,
        )


@unittest.skipIf(os.environ.get('CI') is None, 'not in CI')
@unittest.skipIf(not shutil.which('n'), 'nem is not installed')
class TestCLIInit(unittest.TestCase):

    def setUp(self):
        dbfile = pathlib.Path.home() / '.config' / '.nem.toml'
        if file_exists(dbfile):
            old_dbfile = safe_rm_file(dbfile)
            self.old_dbfile = str(old_dbfile)
        else:
            self.old_dbfile = None

    def tearDown(self):
        if self.old_dbfile:
            restore_file(self.old_dbfile)

    def test_init_creates_dbfile(self):
        dbfile = pathlib.Path.home() / '.config' / '.nem.toml'
        assert_nem(
            '',
            ('', Any(bytes), b'Warning: Output is not to a terminal (fd=1).\n'),
            returncode=0,
        )
        assert_file_exists(dbfile)
