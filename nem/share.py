import os


SOCK = os.environ.get('NEM_SOCK', 'ipc:///tmp/nem.sock')


class CODE:
    EXEC = 1
