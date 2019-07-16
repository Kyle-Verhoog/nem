import logging
import os
from os.path import expanduser
from pathlib import Path
import sys

import colouredlogs
from prompt_toolkit import HTML, print_formatted_text as print
import tabulate
import toml

from .ptdb import Column, Db, DbError, NoResultFound, Model, Schema


DB_FILE = os.environ.get('NEM_DB', str(Path.home().absolute() / '.config' / '.nem.toml'))
DB_FILE = str(Path(DB_FILE).absolute())


log = logging.getLogger(__name__)
colouredlogs.install(
    logger=log,
    level=logging.WARN,
    stream=sys.stdout,
    format='%(asctime)s.%(msecs)03d %(levelname)s %(module)s - %(funcName)s: %(message)s',
    datefmt='%H:%M:%S',
)


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
        s = ctx.get('sess')
        cmds = s.query(Command).all()
        codes_cmds = { cmd.code: cmd.cmd for cmd in cmds }
        cmd = ' '.join(args)
        code = mkcode(cmd, codes_cmds)
        s.add(Command(cmd=cmd, code=code, desc='', freq=0))
        return mkresp(out=f'<ansigreen>added command:</ansigreen> <ansiblue>{code}</ansiblue> = {cmd}')

    def edit(self, opts, args, ctx):
        s = ctx.get('sess')
        code = args[0]
        new_code = args[1]
        try:
            cmd = s.query(Command).filter_by(code=code).one()
            cmd.code = new_code
            return mkresp(out=f'<ansigreen>command <ansiyellow>{cmd.cmd}</ansiyellow> code updated <ansired>{code}</ansired> -> <ansiblue>{new_code}</ansiblue></ansigreen>')
        except NoResultFound:
            return err(out=f'<ansired>code <ansiblue>{code}</ansiblue> not found</ansired>')

    def remove(self, opts, args, ctx):
        s = ctx.get('sess')
        code = args[0]
        try:
            cmd = s.query(Command).filter_by(code=code).one()
            s.delete(cmd)
            return mkresp(out=f'<ansigreen>removed command <ansired>{cmd.cmd}</ansired> with code</ansigreen> <ansiblue>{cmd.code}</ansiblue>')
        except NoResultFound:
            return err(out=f'<ansired>command for code <ansiblue>{code}</ansiblue> not found</ansired>')


class CmdTable(Resource):

    def list(self, opts, args, ctx):
        s = ctx.get('sess')
        rows = s.query(Command).all()
        headers = ['command', 'code', 'usages']

        def _format(rows):
            if 'v' in opts:
                return [[f'{cmd.cmd}', f'[{cmd.code}]', f'{cmd.freq}'] for cmd in rows]
            else:
                return [[f'{cmd.cmd}', f'[{cmd.code}]'] for cmd in rows]

        data = _format(rows)
        table = tabulate.tabulate(
            data,
            headers=headers,
            tablefmt='fancy_grid',
        )
        table = table.replace('[', '[<ansiblue>')
        table = table.replace(']', '</ansiblue>]')
        return mkresp(out=table)


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
    s = ctx.get('sess')
    if len(args) < 1:
        args.append('/tl')

    cmd = args[0]

    if cmd.startswith('/'):
        resp = Resources.interpret(cmd[1:], args[1:], ctx)
        if resp:
            return resp

    try:
        cmd = s.query(Command).filter_by(code=cmd).one()
    except NoResultFound:
        return err(out=f'<ansired>unknown command:</ansired> {cmd}')

    ex_cmd = cmd.cmd
    # if there are args, fill them in
    if len(args) > 1:
        ex_cmd = cmd_w_args(ex_cmd, args[1:])
    cmd.freq += 1
    return mkresp(out=f'<ansigreen>exec:</ansigreen> {ex_cmd}', code=CODE.EXEC, ctx={'cmd': ex_cmd})


def nem():
    db = Db(toml, NemSchema, dbfiles=[DB_FILE])
    db.load()
    session = db
    ctx = {
        'pwd': os.environ.get('PWD'),
        'sess': session
    }
    args = sys.argv[1:]

    (out, code, ctx) = handle_req(args, ctx)

    if code == CODE.EXEC:
        print(HTML(out))
        os.system(ctx['cmd'])
    else:
        print(HTML(out))

    session.commit()
