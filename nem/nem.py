from functools import wraps
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
from . import __version__


# Note the 'root' is not actually at the root directory and is a special case
DB_FILE = os.environ.get('NEM_ROOT_DB', str(Path.home().absolute() / '.config' / '.nem.toml'))
DB_FILE = str(Path(DB_FILE).absolute())


log = get_logger(__name__)


class CODE:
    ERR = -1
    EXEC = 1


class Ignore(Model):
    __table__ = 'ignore'

    item = Column()  # eg. ~/.nem.toml, git*
    type = Column()  # eg. dbfile,      cmdrule


class Include(Model):
    __table__ = 'ignore'

    item = Column()  # eg. ~/.nem.toml, '~/.config/nem/.git.nem.toml'


class Command(Model):
    __table__ = 'cmds'

    cmd = Column()
    code = Column()
    desc = Column()

    def __repr__(self):
        return f'<Command(cmd={self.cmd} code={self.code})>'


class NemSchema(Schema):
    version = __version__
    cmds = Command


def mkresp(out='', code=0, ctx=None):
    log.info(f'creating response "{out}" {code} {ctx}')
    ctx = ctx or {}
    return (out, code, ctx)


def err(**kwargs):
    out = f'<ansired>{kwargs.get("out")}</ansired>'
    kwargs.update(code=CODE.ERR, out=out)
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

    cmd_pieces = str(cmd).split(' ')
    code = ''
    if len(cmd_pieces) > 1 and cmd_pieces[0] in ['n', 'nem']:
        if len(cmd_pieces) > 2:
            code = cmd_pieces[1]
            cmd_pieces = cmd_pieces[2:]
        else:
            cmd_pieces = cmd_pieces[1:]

    code += ''.join([_pick_letter(s) for s in cmd_pieces])
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
        doc = ''
        for attr in self._attrs:
            m = getattr(self, attr)
            doc += f' <ansiblue>{resname[0]}</ansiblue>{resname[1:]}\n'
            doc += f'  <ansiblue>{attr[0]}</ansiblue>{attr[1:]}:\n'
            if m.__doc__:
                _doc = '\n    '.join([dedent(d) for d in m.__doc__.split('\n')])
                _doc = _doc
            else:
                _doc = 'TODO'
            doc += f'    {_doc.strip()}'
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


def opt(**_kwargs):
    # Decorator for documenting and validating opts to a resource
    def dec(f):
        f.__doc__ += f'\n<ansiblue>{_kwargs.get("name")}</ansiblue>: {_kwargs.get("desc")}'
        @wraps(f)
        def wrapper(*args, **kwargs):
            return f(*args, **kwargs)
        return wrapper
    return dec


def arg(**_kwargs):
    # Decorator for documenting and validating arguments to a resource
    def dec(f):
        f.__doc__ += f'\n<ansiblue>{_kwargs.get("name")}</ansiblue>: {_kwargs.get("desc")}'
        @wraps(f)
        def wrapper(*args, **kwargs):
            return f(*args, **kwargs)
        return wrapper
    return dec


class NemRes(Resource):
    __resname__ = 'nem'

    # @argument(name='', description='', validate=)
    def init(self, opts, args, ctx):
        """
        Initializes a new empty nem file in each of the paths provided.
        """
        db = ctx.get('db')
        dbfiles = args
        db.load(dbfiles=dbfiles)
        str_dbfiles = ' '.join(dbfiles)
        return mkresp(out=f'<ansigreen>successfully created dbfile{s if len(dbfiles) > 1 else ""} "{str_dbfiles}"</ansigreen>')


class CmdRes(Resource):
    __resname__ = 'Command'

    # @arg_parse parses args for
    def create(self, opts, args, ctx):
        """
        Creates a command
        """
        db = ctx.get('db')
        cmds = db.query(Command).all(in_dbs=[db.closest])
        codes_cmds = { cmd.code: cmd.cmd for cmd in cmds }
        cmd = ' '.join(args)
        code = mkcode(cmd, codes_cmds)
        db.add(Command(cmd=cmd, code=code, desc=''), in_dbs=['closest'])
        return mkresp(out=f'<ansigreen>added command:</ansigreen> <ansiblue>{code}</ansiblue> = <ansiyellow>{cmd}</ansiyellow><ansigreen> to {db.closest}</ansigreen>')

    # @arg(name='code', position='0', desc='code of the command to document')
    # @arg(name='documentation', position='1', desc='the documentation to give the command')
    def document(self, opts, args, ctx):
        """
        Documents a command
        """
        pass

    def edit(self, opts, args, ctx):
        """
        Edits the code for a command.
        """
        db = ctx.get('db')
        code = args[0]
        new_code = args[1]
        try:
            cmd = db.query(Command).filter_by(code=code).one()
            cmd.code = new_code
            return mkresp(out=f'<ansigreen>command <ansiyellow>{cmd.cmd}</ansiyellow> code updated <ansired>{code}</ansired> -> <ansiblue>{new_code}</ansiblue></ansigreen>')
        except NoResultFound:
            return err(out=f'<ansired>code <ansiblue>{code}</ansiblue> not found</ansired>')

    # @arg(name='query', position='*', desc='what to search for')
    def find(self, opts, args, ctx):
        """
        Finds all commands matching <ansiyellow>query</ansiyellow>
        """
        db = ctx.get('db')
        codes = resolve_codes(db)
        searchstr = ' '.join(args)

        def _matches(cmd, query):
            if query in cmd['cmd']:
                return True
            return False
        matches = [[cmd['cmd'], f'[{code}]', cmd['desc']] for code, cmd in codes.items() if _matches(cmd, searchstr)]
        headers = ['command', 'code', 'description']
        table = mktable(matches, headers=headers)
        table = table.replace('[', '[<ansiblue>')
        table = table.replace(']', '</ansiblue>]')
        return mkresp(out=f'{table}')

    @opt(name='v', desc='verbose mode - prints extra data like the nem file the command was found in')
    def list(self, opts, args, ctx):
        """
        Lists all commands from in nem files in a table.
        """
        # Want most relevant (closer) dbfiles listed at bottom
        db = ctx.get('db')
        codes = resolve_codes(db)

        table_rows = []
        codes_left = set(codes.keys())
        rev_dbnames = list(db.dbnames)
        rev_dbnames.reverse()
        for dbname in rev_dbnames:
            dbcodes = [code for code, meta in codes.items() if dbname in meta['dbs'] and code in codes_left]
            codes_left = codes_left - set(dbcodes)
            for code in dbcodes:
                meta = codes[code]
                cmd = meta['cmd']
                desc = meta['desc']
                dbids = [str(db.dbname_to_i(dbname)) for dbname in meta['dbs']]
                if 'v' in opts:
                    sources = ','.join(dbids)
                    table_rows.append([f'{cmd}', f'{sources}', f'[{code}]', desc])
                else:
                    table_rows.append([f'{cmd}', f'[{code}]'])

        if 'v' in opts:
            headers = ['command', 'file', 'code', 'description']
        else:
            headers = ['command', 'code']

        table = mktable(table_rows, headers=headers)
        table = table.replace('[', '[<ansiblue>')
        table = table.replace(']', '</ansiblue>]')
        if 'v' in opts:
            dbs = '\n'.join([f'(db {db.dbname_to_i(dbname)}) {dbname}' for dbname in db.dbnames])
            dbs += '\n'
        else:
            dbs = ''
        return mkresp(out=f'{dbs}{table}')

    def remove(self, opts, args, ctx):
        db = ctx.get('db')
        code = args[0]
        try:
            cmd = db.query(Command).filter_by(code=code, _in_dbs=[db.closest]).one()
            db.delete(cmd, in_dbs=[db.closest])
            return mkresp(out=f'<ansigreen>removed command <ansired>{cmd.cmd}</ansired> with code</ansigreen> <ansiblue>{cmd.code}</ansiblue>')
        except NoResultFound:
            return err(out=f'<ansired>command for code <ansiblue>{code}</ansiblue> not found</ansired> in nem file {db.closest}')


class Resources:
    class _Resources:
        commands = CmdRes()
        nem = NemRes()
        help = Help()

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
        args.append('/cl')

    cmd = args[0]

    if cmd.startswith('/'):
        resp = Resources._interpret(cmd[1:], args[1:], ctx)
        if resp:
            return resp

    # if finding a resource falls through, interpret the first arg
    # as a code
    code = args[0]

    try:
        codes = resolve_codes(db)
        ex_cmd = codes[code]['cmd']
    except (IndexError, KeyError):
        return err(out=f'<ansired>unknown command:</ansired> {code}')


    # if there are args, fill them in
    if len(args) > 1:
        # quote all args
        _args = ' '.join(list(map(lambda x: f'"{x}"', args[1:])))
        # _args = ' '.join(args[1:])
        ex_cmd = f'{ex_cmd} {_args}'
        # ex_cmd = cmd_w_args(ex_cmd, args[1:])
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

    dbs.append(DB_FILE)
    log.debug(f'gathered dbs {dbs}')
    return dbs


def resolve_codes(db):
    cursor = db.query(Command)
    codes = {}
    for dbname, row in cursor.rows():
        row_code = row['code']
        if row_code not in codes:
            codes[row_code] = dict(
                dbs=[dbname],
                cmd=row['cmd'],
                desc=row['desc'],
            )
        elif codes[row_code]['cmd'] == row['cmd']:
            # duplicate entry, add to the dbs list
            codes[row_code]['dbs'].append(dbname)
        else:
            # for conflicts, postfix db code to code until no conflicts
            i = db.dbname_to_i(dbname)
            code = f'{row_code}{i}'
            while code not in codes:
                code = f'{code}{i}'
            codes[code] = dict(
                dbs=[dbname],
                cmd=row['cmd'],
                desc=row['desc'],
            )
    return codes


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

        was_err = code == CODE.ERR
        print(HTML(out), file=sys.stderr if was_err else sys.stdout)

        if code == CODE.EXEC:
            os.system(ctx['cmd'])

        db.commit()

        sys.exit(1 if was_err else 0)
    except DbError:
        log.error('', exc_info=True)
        print(HTML(f'<ansired>a database error has occurred:\n{traceback.format_exc()}</ansired>'))
    except KeyboardInterrupt:
        pass
