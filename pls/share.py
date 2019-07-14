import os

SOCK = os.environ.get('PLS_SOCK', 'ipc:///tmp/pls.sock')


class CODE:
    EXEC = 1
