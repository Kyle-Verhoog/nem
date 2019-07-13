import os
import logging
import pathlib
import pickle
import sys
import traceback

import coloredlogs
import msgpack
import sqlite3
import zmq

from pls.share import SOCK

logging.basicConfig(stream=sys.stdout, level=logging.INFO)
log = logging.getLogger(__name__)
coloredlogs.install(logger=log)

connection = sqlite3.connect('db')
cursor = connection.cursor()

context = zmq.Context()
sock = context.socket(zmq.REP)
sock.bind(SOCK)


class PlsException(Exception):
    pass

class ExitInterrupt(PlsException):
    pass

class RestartInterrupt(PlsException):
    pass

def resp(out='', err=0, ctx=None):
    ctx = ctx or {}
    return msgpack.packb((out, err, ctx))


def err(**kwargs):
    kwargs.update(err=-1)
    return resp(**kwargs)


def handle_req(req):
    log.info(f'req: {req}')
    args = req['args']
    cmd = args[1]

    if cmd in ['--add', '-a', 'a']:
        alias, cmd = args[2], ' '.join(args[3:])
        return resp(out=f'<ansigreen>added command {alias} = {cmd}</ansigreen>')
    elif cmd in ['--fail']:
        raise Exception()
    elif cmd == '--stop':
        raise ExitInterrupt()
    elif cmd in ['--restart']:
        raise RestartInterrupt()
    else:
        log.info(f'unknown command {cmd}')
        return err(out=f'<ansired>unknown command:</ansired> {cmd}')


def server():
    log.info(f'server started')
    try:
        stay_alive = True
        while stay_alive:
            msg = sock.recv()
            try:
                req = msgpack.unpackb(msg, raw=False)
                res = handle_req(req)
                sock.send(res)
            except PlsException as e:
                raise e
            except Exception:
                sock.send(err(out=f'<ansired>{traceback.format_exc()}</ansired>'))
    except ExitInterrupt:
        log.warn(f'user-induced shutdown')
        sock.send(err())
    except RestartInterrupt:
        log.warn(f'user-induced restart')
        log.info(f'restarting...')
        sock.send(resp('<ansigreen>server restarted</ansigreen>'))
        shutdown()
        ex = sys.executable
        os.execl(ex, ex, *sys.argv)
    except KeyboardInterrupt:
        log.warn(f'keyboard interrupt recvd')
    except Exception as e:
        log.error(f'unexpected shutdown: {e}')
        log.error(traceback.format_exc())
        sock.send(err(out=f'<ansired>{traceback.format_exc()}</ansired>'))
    finally:
        shutdown()
        log.warn(f'exiting...')

def shutdown():
    log.warn(f'shutting down initiated')
    connection.close()
    socket.close()
    context.term()
    log.warn(f'shutting down complete')

