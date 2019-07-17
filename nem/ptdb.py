"""
plain-text db

TODO
- versions
- migrations
- basic caching
- async commits
- tests lol
"""
import logging
import os

from .log import get_logger
from . import __version__


log = get_logger(__name__)


class DbError(Exception):
    pass


class NoResultFound(DbError):
    pass



class Schema:
    @classmethod
    def attrs(cls):
        return [attr for attr in cls.__dict__ if not attr.startswith('_')]

    @classmethod
    def tablenames(cls):
        return [attr for attr in cls.attrs() if Model.ismodel(getattr(cls, attr))]

    @classmethod
    def from_data(cls, data):
        for attr in cls.attrs():
            field = getattr(cls, attr)
            if Model.ismodel(field):
                data['__table__'][attr] = field.from_raw_table(data['__table__'][attr])
            else:
                # if attr not in data:
                data[attr] = field
        return data


class Model:
    __table__ = ''

    def __init__(self, *args, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

    @classmethod
    def ismodel(cls, c):
        return isinstance(c, type) and issubclass(c, cls)

    @classmethod
    def attrs(cls):
        return [attr for attr in cls.__dict__ if not attr.startswith('_')]

    def to_raw_row(self):
        return {
            attr: getattr(self, attr)
            for attr in self.__class__.attrs()
        }

    def eq_raw_row(self, row):
        eq = True
        for attr in self.__class__.attrs():
            field = getattr(self, attr)
            if field != row[attr]:
                return False
        return eq

    @classmethod
    def from_raw_table(cls, table):
        return [cls(row) for row in table]


class Column:
    pass


class QuerySet(list):
    def __init__(self, model, rows):
        self._rows = rows
        self._model = model
        super().__init__(rows)

    def one(self):
        # TODO: should raise if > 1 row
        try:
            return self._model(**self._rows[0])
        except IndexError:
            raise NoResultFound


class Cursor:
    def __init__(self, model, dbtables):
        self._model = model
        self._dbtables = dbtables

    def _rows(self, in_dbs=None):
        dbnames = in_dbs or set(self._dbtables.keys())
        for dbname, table in self._dbtables.items():
            if dbname not in dbnames:
                continue

            for row in table:
                yield row

    def all(self):
        return [self._model(**row) for row in self._rows()]

    def filter_by(self, **kwargs):
        in_dbs = kwargs.pop('_in_dbs', None)
        def _row_matches(row):
            matches = True
            for k, v in kwargs.items():
                if row[k] != v:
                    return False
            return matches
        return QuerySet(self._model, [row for row in self._rows(in_dbs) if _row_matches(row)])


class Db:
    def __init__(self, lib, schema, dbfiles=None):
        self._dbfiles = dbfiles
        self.lib = lib
        self.schema = schema
        self._dbs = {}

    @property
    def dbnames(self):
        return self._dbfiles

    def empty_db(self):
        return {
            'version': __version__,
            '__table__': {
                tablename: [] for tablename in self.schema.tablenames()
            },
        }

    def load(self, dbfiles=None):
        if not dbfiles and not self._dbfiles:
            raise DbError('no dbfile specified')
        dbfiles = dbfiles or self._dbfiles

        raw_stores = {}
        for dbfile in dbfiles:
            log.debug(f'loading dbfile {dbfile}')
            if os.path.exists(dbfile) and not os.path.isfile(dbfile):
                raise DbError(f'dbfile {dbfile} is a directory')
            if not os.path.exists(dbfile):
                empty_db = self.lib.dumps(self.empty_db())
                # Write the file so that it exists even if we crash later on
                with open(dbfile, 'w') as f:
                    print(empty_db, file=f)

            with open(dbfile, 'r') as f:
                try:
                    raw_stores[dbfile] = f.read()
                except toml.decoder.DecodeError as e:
                    log.error(exc_info=True)
                    raise DbError(f'Failed to read dbfile {dbfile}') from e

        for db, raw in raw_stores.items():
            raw_db = self.lib.loads(raw)
            # self._dbs[db] = self.schema.from_data(raw_db)
            self._dbs[db] = raw_db

    def query(self, model, dbopts=None):
        tables = {
            dbname: db['__table__'][model.__table__]
            for dbname, db in self._dbs.items()
        }
        return Cursor(model, tables)

    def commit(self):
        for dbname, db in self._dbs.items():
            out = self.lib.dumps(db)
            with open(dbname, 'w') as f:
                print(out, file=f)

    def add(self, inst, dbopts=None):
        cls = inst.__class__
        tablename = cls.__table__

        for dbname, db in self._dbs.items():
            db['__table__'][tablename].append(inst.to_raw_row())

    def delete(self, inst, dbopts=None):
        cls = inst.__class__
        tablename = cls.__table__

        for dbname, db in self._dbs.items():
            new_rows = [row for row in db['__table__'][tablename] if not inst.eq_raw_row(row)]
            db['__table__'][tablename] = new_rows
