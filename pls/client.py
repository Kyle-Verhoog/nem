import sys
from os import environ as env, system
import msgpack
from prompt_toolkit import HTML, prompt, print_formatted_text as print
import zmq

from pls.share import CODE, SOCK


def get_context():
    return {
        'pwd': env.get('PWD'),
    }


def client():
    try:
        context = zmq.Context()
        sock = context.socket(zmq.REQ)
        sock.connect(SOCK)
        ctx = get_context()
        sock.send(msgpack.packb({
            'args': sys.argv,
            **ctx,
        }))
        reply = msgpack.unpackb(sock.recv(), raw=False)
        stdout, code, ctx = reply
        if code == CODE.EXEC:
            # prompt(HTML(stdout), vi_mode=True)
            print(HTML(stdout))
            system(ctx['cmd'])
        else:
            print(HTML(stdout))
    except Exception as e:
        raise e
    finally:
        sock.close()
        context.term()
