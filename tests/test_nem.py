import unittest

from nem.nem import Command


class TestCommand(unittest.TestCase):
    def test_fmt_cmd(self):
        self.assertEqual(
            Command._fmt_cmd('git push {r=origin} {b=master}'),
            ('git push <ansiyellow>{r=origin}</ansiyellow> '
             '<ansiyellow>{b=master}</ansiyellow>'
             )
        )


class TestNem(unittest.TestCase):
    pass
