from itertools import zip_longest
import logging
import os
import pathlib
import pickle
import sys
import threading
import traceback

import colouredlogs
import msgpack
import sqlite3
import tabulate
import zmq

from pls.share import CODE, SOCK


"""
COMMAND CODE

COMMAND PWD TIMESTAMP
"""
DATABASE = 'db'


log = logging.getLogger(__name__)
colouredlogs.install(
    logger=log,
    level=logging.INFO,
    stream=sys.stdout,
    format='%(asctime)s.%(msecs)03d %(levelname)s %(module)s - %(funcName)s: %(message)s',
    datefmt='%H:%M:%S',
)
connection = None
cursor = None
context = None
sock = None


class PlsException(Exception):
    pass


class ExitInterrupt(PlsException):
    pass


class RestartInterrupt(PlsException):
    pass


def resp(out='', code=0, ctx=None):
    log.info(f'creating response "{out}" {code} {ctx}')
    ctx = ctx or {}
    return msgpack.packb((out, code, ctx))


def err(**kwargs):
    kwargs.update(code=-1)
    return resp(**kwargs)


class Alias:
    prefix = 'a'

    def create(self, args):
        pass


class Command:
    prefix = 'c'

    def create(self, args):
        pass


class CmdTable:
    def lookup(self, code):
        pass

    def _code(self, cmd, sofar):
        if not cmd:
            return ''

        def _pick_letter(word):
            l = list(word)
            while l:
                if l[0].isalpha():
                    return l[0]
                else:
                    l = l[1:]
            return ''

        code = ''.join([_pick_letter(s) for s in str(cmd).split(' ')])
        while code in sofar:
            code += 'f'
        return code

    def _format(self, rows):
        return [[f'{cmd} [{code}]'] for (cmd, code) in rows]

    def list(self):
        cursor.execute('select * from cmds')
        rows = cursor.fetchall()
        headers = ['commands']
        data = self._format(rows)
        table = tabulate.tabulate(
            data,
            headers=headers,
            tablefmt='fancy_grid',
        )
        table = table.replace('[', '[<ansiblue>')
        table = table.replace(']', '</ansiblue>]')
        return resp(out=table)


class Resources:
    alias = Alias()
    table = CmdTable()


def handle_req(req):
    log.info(f'req: {req}')
    args = req['args']

    if len(args) < 2:
        return Resources.table.list()

    cmd = args[1]

    ctx = {}
    # create commands
    if cmd.startswith('c'):
        action = cmd[1]
        if action in ['a']:
            if 'h' in cmd:
                ctx['pwd'] = os.environ.get('PWD')
            else:
                ctx['pwd'] = '/home'
            alias, cmd = args[2], ' '.join(args[3:])
            db[alias] = cmd
            return resp(out=f'<ansigreen>added command:</ansigreen> [{ctx["pwd"]}] <ansiblue>{alias}</ansiblue> = {cmd}')
    elif cmd.startswith('l'):
        action = cmd[1]
        if action in ['a']:
            aliases = '\n'.join([f'<ansiblue>{key}</ansiblue> = {value}' for key, value in db.items()])
            return resp(out=f'<ansigreen>aliases:</ansigreen>\n{aliases}')
    elif cmd.startswith('r'):
        raise RestartInterrupt()
    elif cmd.startswith('+'):
        cursor.execute("""
        create table if not exists cmds(
            cmd text,
            code text primary key
        )""")
        # cursor.execute("""
        # create table if not exists usages(
        #     code text primary key,
        #     pwd text,
        #     timestamp text
        # )""")
        cursor.execute("""insert into cmds(cmd, code) values ("git status", "gs");""")
        cursor.execute("""insert into cmds(cmd, code) values ("git add -u", "gau");""")
        cursor.execute("""insert into cmds(cmd, code) values ("git diff --staged", "gds");""")
        cursor.execute("""insert into cmds(cmd, code) values ("git diff", "gd");""")
        connection.commit()
        return resp(out=f'<ansigreen>db seeded</ansigreen>')
    elif cmd.startswith('-'):
        cursor.execute("""drop table cmds""")
        # cursor.execute("""drop table usages""")
        connection.commit()
        return resp(out=f'<ansigreen>db cleared</ansigreen>')
    elif cmd.startswith('e'):
        q = args[2]
        if q not in db:
            return err(out=f'<ansired>alias or command</ansired> {q} <ansired>not found</ansired>')
        cmd = db[q]
        return resp(out=f'<ansigreen>exec:</ansigreen> {cmd}', code=CODE.EXEC, ctx={'cmd': cmd})
    elif cmd in ['--add', '-a', 'a']:
        pass
    elif cmd == '--stop':
        raise ExitInterrupt()
    elif cmd in ['--restart']:
        raise RestartInterrupt()

    cursor.execute('select * from cmds where code=?', (cmd, ))
    rows = cursor.fetchall()
    if rows:
        cmd, code = rows[0]
        return resp(out=f'<ansigreen>exec:</ansigreen> {cmd}', code=CODE.EXEC, ctx={'cmd': cmd})
    else:
        log.info(f'unknown command {cmd}')
        return err(out=f'<ansired>unknown command:</ansired> {cmd}')


def server():
    log.info(f'server started')
    global connection, cursor, context, sock

    try:
        connection = sqlite3.connect(DATABASE)
        cursor = connection.cursor()
        context = zmq.Context()
        sock = context.socket(zmq.REP)
        sock.bind(SOCK)

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
        shutdown(exit=True)


def shutdown(exit=False, exit_code=0):
    log.warn(f'shutting down initiated')
    connection.close()
    sock.close()
    context.term()
    log.warn(f'shutting down complete')

    if exit:
        log.warn(f'exiting...')
        sys.exit(exit_code)

