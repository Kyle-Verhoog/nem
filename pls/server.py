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
    def handle(self, cmd, args, ctx):
        try:
            attr = [attr for attr in dir(self) if not attr.startswith('__') and attr.startswith(cmd[0])]
            handler = getattr(self, attr[0])
            return handler(cmd[1:], args, ctx)
        except Exception:
            log.error(f'failed on command {cmd}', exc_info=True)
            return err(out=f'command {cmd} failed or does not exist on resource {self.__class__.__name__}')


class CmdManager(Resource):
    def create(self, opts, args, ctx):
        pwd = ctx.get('pwd')
        cur = ctx.get('cur')
        conn = ctx.get('conn')
        cursor.execute('select cmd, code from cmds')
        codes_cmds = { code: cmd for cmd, code in cursor.fetchall() }
        cmd = ' '.join(args)
        code = mkcode(cmd, codes_cmds)
        cur.execute('insert into cmds(cmd, code, desc, freq) values (?, ?, ?, ?);', (cmd, code, '', 0))
        conn.commit()
        return mkresp(out=f'<ansigreen>added command:</ansigreen> <ansiblue>{code}</ansiblue> = {cmd}')

    def remove(self, opts, args, ctx):
        code = args[0]
        cursor.execute('select cmd, code from cmds where code=?', (code, ))
        r = cursor.fetchall()
        if len(r) < 1:
            return err(out=f'<ansired>command for code {code} not found</ansired>')
        cmd, code = r[0]
        cursor.execute('delete from cmds where code=?', (code, ))
        connection.commit()
        return mkresp(out=f'<ansigreen>removed command {cmd} for code</ansigreen> <ansiblue>{code}</ansiblue>')


class CmdTable(Resource):

    def list(self, opts, args, ctx):
        cursor.execute('select cmd, code, freq from cmds')
        rows = cursor.fetchall()
        headers = ['command', 'code']

        def _format(rows):
            if 'v' in opts:
                return [[f'{cmd} [{code}] ({freq} usages)'] for (cmd, code, freq) in rows]
            else:
                return [[f'{cmd}', f'[{code}]'] for (cmd, code, _) in rows]

        data = _format(rows)
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
            freq integer,
            arginfo blob
        )""")
        cursor.execute('create table if not exists version(version text)')
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

    def handle(self, cmd, args, ctx):
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
    def interpret(cls, cmd, args, ctx):
        resource = [r for r in dir(cls) if not r.startswith('__') and r.startswith(cmd[0])]
        if not resource or not hasattr(cls, resource[0]):
            return None
        resource = getattr(cls, resource[0])
        return resource.handle(cmd[1:], args, ctx)


def cmd_w_args(cmd, args):
    kwargs = {
        f'arg{i+1}': arg for i, arg in enumerate(args)
    }
    return cmd.format(**kwargs)


def handle_req(req):
    log.info(f'req: {req}')
    ctx = req
    ctx.update({'cur': cursor, 'conn': connection})
    args = req.get('args')

    if len(args) < 2:
        args.append('/tl')

    cmd = args[1]

    if cmd.startswith('/'):
        resp = Resources.interpret(cmd[1:], args[2:], ctx)
        if resp:
            return resp

    cursor.execute('select cmd, code, freq from cmds where code=?', (cmd, ))
    rows = cursor.fetchall()
    if rows:
        cmd, code, freq = rows[0]

        # if there are args, fill them in
        if len(args) > 1:
            cmd = cmd_w_args(cmd, args[2:])
        cursor.execute('update cmds set freq=? where code=?', (freq+1, code))
        # cursor.execute('insert into usages(cmd, code, desc, freq) values ("git status", "gs", "", 0)')
        connection.commit()
        return mkresp(out=f'<ansigreen>exec:</ansigreen> {cmd}', code=CODE.EXEC, ctx={'cmd': cmd})
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

