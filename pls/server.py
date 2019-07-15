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


def mkresp(out='', code=0, ctx=None):
    log.info(f'creating response "{out}" {code} {ctx}')
    ctx = ctx or {}
    return msgpack.packb((out, code, ctx))


def err(**kwargs):
    kwargs.update(code=-1)
    return mkresp(**kwargs)

def mkcode(cmd, codes):
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
    while code in codes:
        code += 'f'
    return code


class Resource:
    def handle(self, cmd, args):
        try:
            attr = [attr for attr in dir(self) if not attr.startswith('__') and attr.startswith(cmd[0])]
            handler = getattr(self, attr[0])
            return handler(cmd[1:], args)
        except Exception:
            log.error(f'failed on command {cmd}', exc_info=True)
            return err(out=f'command {cmd} does not exist on resource {self.__class__.__name__}')


class CmdManager(Resource):
    def create(self, opts, args):
        pwd = os.environ.get('PWD')
        cursor.execute('select cmd, code from cmds')
        codes_cmds = { code: cmd for cmd, code in cursor.fetchall() }
        cmd = ' '.join(args)
        code = mkcode(cmd, codes_cmds)
        cursor.execute('insert into cmds(cmd, code, desc, freq) values (?, ?, ?, ?);', (cmd, code, '', 0))
        return mkresp(out=f'<ansigreen>added command:</ansigreen> <ansiblue>{code}</ansiblue> = {cmd}')


class CmdTable(Resource):
    def _format(self, rows):
        return [[f'{cmd} [{code}]'] for (cmd, code) in rows]

    def list(self, opts, args):
        cursor.execute('select cmd, code from cmds')
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
        return mkresp(out=table)


class ServerControl():
    def restart(self):
        raise RestartInterrupt()

    def dbinit(self):
        cursor.execute("""
        create table if not exists cmds(
            cmd text,
            code text primary key,
            desc text,
            freq integer
        )""") # description
        # cursor.execute("""
        # create table if not exists usages(
        #     code text primary key,
        #     pwd text,
        #     timestamp text
        # )""")
        cursor.execute('insert into cmds(cmd, code, desc, freq) values ("git status", "gs", "", 0);')
        cursor.execute('insert into cmds(cmd, code, desc, freq) values ("git add -u", "gau", "", 0);')
        cursor.execute('insert into cmds(cmd, code, desc, freq) values ("git diff --staged", "gds", "", 0);')
        cursor.execute('insert into cmds(cmd, code, desc, freq) values ("git diff", "gd", "", 0);')
        connection.commit()
        return mkresp(out=f'<ansigreen>db seeded</ansigreen>')

    def dbrm(self):
        cursor.execute("""drop table cmds""")
        # cursor.execute("""drop table usages""")
        connection.commit()
        return mkresp(out=f'<ansigreen>db cleared</ansigreen>')

    def stop(self):
        raise ExitInterrupt()

    def handle(self, cmd, args):
        if cmd.startswith('r'):
            return self.restart()
        if cmd.startswith('s'):
            return self.stop()
        if cmd.startswith('+'):
            return self.dbinit()
        if cmd.startswith('-'):
            return self.dbrm()


class Resources:
    commands = CmdManager()
    table = CmdTable()
    server = ServerControl()

    @classmethod
    def interpret(cls, cmd, args):
        resource = [r for r in dir(cls) if not r.startswith('__') and r.startswith(cmd[0])]
        if not resource or not hasattr(cls, resource[0]):
            return None
        resource = getattr(cls, resource[0])
        return resource.handle(cmd[1:], args)


def handle_req(req):
    log.info(f'req: {req}')
    args = req['args']

    if len(args) < 2:
        args.append('.tl')

    cmd = args[1]

    if cmd.startswith('.'):
        resp = Resources.interpret(cmd[1:], args[2:])
        if resp:
            return resp

    ctx = {}
    if cmd.startswith('l'):
        action = cmd[1]
        if action in ['a']:
            aliases = '\n'.join([f'<ansiblue>{key}</ansiblue> = {value}' for key, value in db.items()])
            return mkresp(out=f'<ansigreen>aliases:</ansigreen>\n{aliases}')
    elif cmd.startswith('e'):
        q = args[2]
        if q not in db:
            return err(out=f'<ansired>alias or command</ansired> {q} <ansired>not found</ansired>')
        cmd = db[q]
        return mkresp(out=f'<ansigreen>exec:</ansigreen> {cmd}', code=CODE.EXEC, ctx={'cmd': cmd})
    elif cmd in ['--add', '-a', 'a']:
        pass
    elif cmd in ['--restart']:
        raise RestartInterrupt()

    cursor.execute('select cmd, code, freq from cmds where code=?', (cmd, ))
    rows = cursor.fetchall()
    if rows:
        cmd, code, freq = rows[0]
        cursor.execute('update cmds set freq=? where code=?', (freq+1, code))
        # cursor.execute('insert into usages(cmd, code, desc, freq) values ("git status", "gs", "", 0)')
        connection.commit()
        return mkresp(out=f'<ansigreen>exec:</ansigreen> {cmd} [{freq}]', code=CODE.EXEC, ctx={'cmd': cmd})
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
        sock.send(mkresp('<ansigreen>server restarted</ansigreen>'))
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

