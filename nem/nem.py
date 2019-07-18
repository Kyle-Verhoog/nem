import logging
import os
from os.path import expanduser
from pathlib import Path
import sys
from textwrap import dedent
import traceback

from prompt_toolkit import HTML, print_formatted_text as print, prompt
import toml

from .log import get_logger
from .ptdb import Column, Db, DbError, NoResultFound, Model, Schema
from .table import mktable


# Note the 'root' is not actually at the root directory and is a special case
DB_FILE = os.environ.get('NEM_ROOT_DB', str(Path.home().absolute() / '.config' / '.nem.toml'))
DB_FILE = str(Path(DB_FILE).absolute())


log = get_logger(__name__)


class CODE:
    EXEC = 1


class Ignore(Model):
    __table__ = 'ignore'

    item = Column() # eg. ~/.nem.toml, git*
    type = Column() # eg. dbfile,      cmdrule



class Command(Model):
    __table__ = 'cmds'

    cmd = Column()
    code = Column()
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
    __resname__ = 'Resource'

    def help(self, cmd, args, ctx):
        return mkresp(out=f'TODO: help')

    @property
    def _attrs(self):
        return [attr for attr in dir(self) if not attr.startswith('_')]

    @property
    def _doc(self):
        resname = self.__resname__.lower()
        # doc = f' <ansiblue>{resname[0]}</ansiblue>{resname[1:]}\n'
        doc = ''
        for attr in self._attrs:
            m = getattr(self, attr)
            doc += f' <ansiblue>{resname[0]}</ansiblue>{resname[1:]}\n'
            doc += f'  <ansiblue>{attr[0]}</ansiblue>{attr[1:]}:\n'
            doc += f'      {dedent((m.__doc__ or "TODO").strip())}'
            doc += '\n'
        doc = doc[0:-1]
        return doc

    def _handle(self, cmd, args, ctx):
        try:
            if not cmd:
                handler = self.help
            else:
                attrs = [attr for attr in self._attrs if attr.startswith(cmd[0])]
                handler = getattr(self, attrs[0])
            return handler(cmd[1:], args, ctx)
        except Exception:
            log.warn(f'failed on command {cmd}', exc_info=True)
            return err(out=f'<ansired>command {cmd} failed or does not exist on resource {self.__class__.__resname__}</ansired>')


class Help(Resource):
    __resname__ = 'Help'

    @property
    def _resource_docs(self):
        docs = ''
        for resource in Resources.resources():
            docs += resource._doc
        return docs

    def help(self, cmd, args, ctx):
        doc =\
        """\
        M<ansiblue>NEM</ansiblue>ONICS
        <ansiblue>ttmytabptb</ansiblue>
        trying to make your terminal a better place to be

        <ansiblue>/</ansiblue>
        {resource_docs}
        """
        doc = dedent(doc)
        doc = doc.format(resource_docs=self._resource_docs)
        return mkresp(out=doc)


class CmdRes(Resource):
    __resname__ = 'Command'

    # @arg_parse parses args for
    def create(self, opts, args, ctx):
        """
        Creates a command
        """
        pwd = ctx.get('pwd')
        db = ctx.get('db')
        cmds = db.query(Command).all()
        codes_cmds = { cmd.code: cmd.cmd for cmd in cmds }
        cmd = ' '.join(args)
        code = mkcode(cmd, codes_cmds)
        db.add(Command(cmd=cmd, code=code, desc='', freq=0), in_dbs=['closest'])
        return mkresp(out=f'<ansigreen>added command:</ansigreen> <ansiblue>{code}</ansiblue> = <ansiyellow>{cmd}</ansiyellow><ansigreen> to {db.closest}</ansigreen>')

    def document(self, opts, args, ctx):
        """
        Document a command
        """
        pass

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

    def find(self, opts, args, ctx):
        pass

    def list(self, opts, args, ctx):
        pass

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
        rows.reverse()

        def _format(rows):
            if 'v' in opts:
                return [[f'{cmd.cmd}', f'[{cmd.code}]', f'{cmd.desc}'] for cmd in rows]
            else:
                return [[f'{cmd.cmd}', f'[{cmd.code}]'] for cmd in rows]

        table = mktable(_format(rows), headers=['command', 'code', 'description'])
        table = table.replace('[', '[<ansiblue>')
        table = table.replace(']', '</ansiblue>]')
        dbs = '\n'.join(db.dbnames)
        return mkresp(out=f'{dbs}\n{table}')


class Resources:
    class _Resources:
        commands = CmdRes()
        help = Help()
        table = CmdTable()

    @classmethod
    def resourcenames(cls):
        return [r for r in dir(cls._Resources) if not r.startswith('__')]

    @classmethod
    def resources(cls):
        return [getattr(cls._Resources, resname) for resname in cls.resourcenames()]

    @classmethod
    def hasresource(cls, name):
        return hasattr(cls._Resources, name)

    @classmethod
    def _interpret(cls, cmd, args, ctx):
        resname = [r for r in cls.resourcenames() if r.startswith(cmd[0])]
        if not resname or not cls.hasresource(resname[0]):
            return None
        resource = getattr(cls._Resources, resname[0])
        return resource._handle(cmd[1:], args, ctx)


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
        resp = Resources._interpret(cmd[1:], args[1:], ctx)
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
    d = Path(os.environ.get('PWD'))
    while str(d) != '/':
        log.debug(f'searching for config directory {d}')
        db_file = d / '.nem.toml'
        if os.path.exists(db_file) and os.path.isfile(db_file): # and not in block list
            dbs.append(str(db_file))
        d = d.parent

    if not os.path.exists(DB_FILE):
        print(HTML(f'<ansired>db file <ansiblue>{DB_FILE}</ansiblue> does not exist</ansired>'))
        if prompt('create it [y/n]? ') == 'y':
            dbs.append(DB_FILE)
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
