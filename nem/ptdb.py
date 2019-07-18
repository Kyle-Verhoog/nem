"""
plain-text db

TODO
- versions
- migrations
- basic caching
- async commits
- tests lol
- unique constraints
"""
from collections import OrderedDict
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

    def __init__(self, *args, _db=None, _id=None, **kwargs):
        self._db = _db
        self._id = _id
        self._fields = {}
        self._fields.update(kwargs)

    def __getattr__(self, name):
        return self._attrs[name]

    def _set_field(self, field, value):
        self._db._add_mutation(self._id, field, value)
        self._fields[field] = value
        return value

    def __setattr__(self, name, value):
        if name in {'_db', '_id', '_fields'}:
            return object.__setattr__(self, name, value)

        attrs = object.__getattribute__(self, '_fields')
        if attrs and name in attrs:
            return self._set_field(name, value)
        else:
            return object.__setattribute__(self, name, value)

    def _field_access(self, name):
        return self._fields[name]

    def __getattribute__(self, name):
        attrs = object.__getattribute__(self, '_fields')
        if attrs and name in attrs:
            return self._field_access(name)
        else:
            return object.__getattribute__(self, name)

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
    def __init__(self, db, model, rows):
        self._db = db
        self._rows = rows
        self._model = model
        super().__init__(rows)

    def one(self):
        # TODO: should raise if > 1 row
        try:
            row = self._rows[0]
            return self._model(_db=self._db, _id=id(row), **row)
        except IndexError:
            raise NoResultFound


class Cursor:
    def __init__(self, db, model, dbtables):
        self._db = db
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
        return [self._model(_db=self._db, _id=id(row), **row) for row in self._rows()]

    def filter_by(self, **kwargs):
        in_dbs = kwargs.pop('_in_dbs', None)
        def _row_matches(row):
            matches = True
            for k, v in kwargs.items():
                if row[k] != v:
                    return False
            return matches
        return QuerySet(self._db, self._model, [row for row in self._rows(in_dbs) if _row_matches(row)])


class Db:
    def __init__(self, lib, schema, dbfiles=None):
        self._dbfiles = dbfiles
        self.lib = lib
        self.schema = schema
        self._dbs = OrderedDict()
        self._mutations = {}

    @property
    def dbnames(self):
        return self._dbfiles

    @property
    def rows(self):
        # generator for all rows in all database.. yikes
        for dbfile, db in self._dbs.items():
            for tablename, table in db['__table__'].items():
                for row in table:
                    yield row


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

        # Note that the databases are added implicitly in order of close-ness
        # (because dbfiles are ordered)
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
                    raw = f.read()
                except Exception as e:
                    log.error(exc_info=True)
                    raise DbError(f'Failed to read dbfile {dbfile}') from e

            raw_db = self.lib.loads(raw)
            self._dbs[dbfile] = raw_db

    def _add_mutation(self, _id, field, value):
        if _id not in self._mutations:
            self._mutations[_id] = []
        # TODO: can probably just use a dict for representing
        # all the mutations and then just update relevant fields
        self._mutations[_id].append((field, value))

    def query(self, model, dbopts=None):
        tables = {
            dbname: db['__table__'][model.__table__]
            for dbname, db in self._dbs.items()
        }
        return Cursor(self, model, tables)

    def commit(self):
        # apply mutations
        for row in self.rows:
            row_id = id(row)
            if row_id in self._mutations:
                for field, value in self._mutations[row_id]:
                    row[field] = value

        # persist to file
        for dbname, db in self._dbs.items():
            out = self.lib.dumps(db)
            with open(dbname, 'w') as f:
                print(out, file=f)

    @property
    def closest(self):
        return self.dbnames[0]

    def isclosest(self, dbname):
        return dbname == self.closest

    def add(self, inst, in_dbs=None):
        cls = inst.__class__
        tablename = cls.__table__

        raw_row = inst.to_raw_row()

        # Since the instance was (most-likely) created outside of our control
        # it won't have these fields which are required to trigger mutations
        # on setattr.
        inst._db = self
        inst._id = id(raw_row)

        in_dbs = in_dbs or set(self._dbtables.keys())
        for dbname, db in self._dbs.items():
            # if 'closest' is provided, then match with the 'closest'
            # (directory-wise) dbfile.
            if 'closest' in in_dbs and self.isclosest(dbname):
                db['__table__'][tablename].append(raw_row)
            elif dbname in in_dbs:
                db['__table__'][tablename].append(raw_row)

    def delete(self, inst, dbopts=None):
        cls = inst.__class__
        tablename = cls.__table__

        for dbname, db in self._dbs.items():
            new_rows = [row for row in db['__table__'][tablename] if not inst.eq_raw_row(row)]
            db['__table__'][tablename] = new_rows
