import logging
import os
from os.path import expanduser
from pathlib import Path
import sys
import traceback

from prompt_toolkit import HTML, print_formatted_text as print, prompt
import toml

from .log import get_logger
from .ptdb import Column, Db, DbError, NoResultFound, Model, Schema
from .table import mktable


DB_FILE = os.environ.get('NEM_DB', str(Path.home().absolute() / '.config' / '.nem.toml'))
DB_FILE = str(Path(DB_FILE).absolute())


log = get_logger(__name__)


class CODE:
    EXEC = 1


class Command(Model):
    __table__ = 'cmds'

    cmd = Column()
    code = Column()
    freq = Column()
    desc = Column()

    def __repr__(self):
        return f'<Command(cmd={self.cmd} code={self.code})>'

class NemSchema(Schema):
    version = '0.0.1'
    cmds = Command


def mkresp(out='', code=0, ctx=None):
    log.info(f'creating response "{out}" {code} {ctx}')
    ctx = ctx or {}
    return (out, code, ctx)


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
            elif l[0] in ['{']:
                return ''
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
        db = ctx.get('db')
        cmds = db.query(Command).all()
        codes_cmds = { cmd.code: cmd.cmd for cmd in cmds }
        cmd = ' '.join(args)
        code = mkcode(cmd, codes_cmds)
        db.add(Command(cmd=cmd, code=code, desc='', freq=0))
        return mkresp(out=f'<ansigreen>added command:</ansigreen> <ansiblue>{code}</ansiblue> = {cmd}')

    def edit(self, opts, args, ctx):
        db = ctx.get('db')
        code = args[0]
        new_code = args[1]
        try:
            cmd = db.query(Command).filter_by(code=code).one()
            cmd.code = new_code
            return mkresp(out=f'<ansigreen>command <ansiyellow>{cmd.cmd}</ansiyellow> code updated <ansired>{code}</ansired> -> <ansiblue>{new_code}</ansiblue></ansigreen>')
        except NoResultFound:
            return err(out=f'<ansired>code <ansiblue>{code}</ansiblue> not found</ansired>')

    def remove(self, opts, args, ctx):
        db = ctx.get('db')
        code = args[0]
        try:
            cmd = db.query(Command).filter_by(code=code).one()
            db.delete(cmd)
            return mkresp(out=f'<ansigreen>removed command <ansired>{cmd.cmd}</ansired> with code</ansigreen> <ansiblue>{cmd.code}</ansiblue>')
        except NoResultFound:
            return err(out=f'<ansired>command for code <ansiblue>{code}</ansiblue> not found</ansired>')


class CmdTable(Resource):
    def _list_db(self, opts, args, ctx):
        db = ctx.get('db')

        data = []
        for dbname in db.dbnames:
            rows = db.query(Command).filter_by(_in_dbs=[dbname])
            data.append([dbname, len(rows), ])
        table = mktable(data, headers=['dbfile', '# entries'])
        return mkresp(out=f'{table}')

    def list(self, opts, args, ctx):
        if opts == 'db':
            return self._list_db(opts, args, ctx)

        db = ctx.get('db')
        rows = db.query(Command).all()

        def _format(rows):
            if 'v' in opts:
                return [[f'{cmd.cmd}', f'[{cmd.code}]', f'{cmd.freq}'] for cmd in rows]
            else:
                return [[f'{cmd.cmd}', f'[{cmd.code}]'] for cmd in rows]

        table = mktable(_format(rows), headers=['command', 'code', 'usages'])
        table = table.replace('[', '[<ansiblue>')
        table = table.replace(']', '</ansiblue>]')
        dbs = '\n'.join(db.dbnames)
        return mkresp(out=f'{dbs}\n{table}')


class Resources:
    commands = CmdManager()
    table = CmdTable()

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


def handle_req(args, ctx):
    db = ctx.get('db')
    if len(args) < 1:
        args.append('/tl')

    cmd = args[0]

    if cmd.startswith('/'):
        resp = Resources.interpret(cmd[1:], args[1:], ctx)
        if resp:
            return resp

    try:
        cmd = db.query(Command).filter_by(code=cmd).one()
    except NoResultFound:
        return err(out=f'<ansired>unknown command:</ansired> {cmd}')

    ex_cmd = cmd.cmd
    # if there are args, fill them in
    if len(args) > 1:
        ex_cmd = cmd_w_args(ex_cmd, args[1:])
    cmd.freq += 1
    return mkresp(out=f'<ansigreen>exec:</ansigreen> {ex_cmd}', code=CODE.EXEC, ctx={'cmd': ex_cmd})


def gather_dbfiles():
    dbs = []
    if not os.path.exists(DB_FILE):
        print(HTML(f'<ansired>db file <ansiblue>{DB_FILE}</ansiblue> does not exist</ansired>'))
        if prompt('create it [y/n]? ') == 'y':
            dbs.append(DB_FILE)

    d = Path(os.environ.get('PWD'))

    while str(d) != '/':
        log.debug(f'searching for config directory {d}')
        db_file = d / '.nem.toml'
        if os.path.exists(db_file) and os.path.isfile(db_file): # and not in block list
            dbs.append(str(db_file))
        d = d.parent
    log.debug(f'gathered dbs {dbs}')
    return dbs


def nem():
    try:
        dbs = gather_dbfiles()

        if not dbs:
            print(HTML('<ansired>could not find a db file to use!</ansired>'))
            return

        db = Db(toml, NemSchema, dbfiles=dbs)
        db.load()

        ctx = {
            'pwd': os.environ.get('PWD'),
            'db': db,
        }
        args = sys.argv[1:]
        (out, code, ctx) = handle_req(args, ctx)

        if code == CODE.EXEC:
            print(HTML(out))
            os.system(ctx['cmd'])
        else:
            print(HTML(out))

        db.commit()
    except DbError:
        log.error('', exc_info=True)
        print(HTML('<ansired>a database error has occurred:\n{traceback.format_exc()}</ansired>'))
    except KeyboardInterrupt:
        pass
