from __future__ import with_statement

import collections
import contextlib
import datetime
import functools
import logging
import os
import re
import sys
import threading
import time

import processing.managers

from django.conf import settings
from openorg_timeseries.database import TimeSeriesDatabase

logger = logging.getLogger(__name__)

class TimeSeriesException(Exception): pass
class ClientError(TimeSeriesException): pass
class SeriesNotFound(ClientError): pass
class SeriesAlreadyExists(ClientError): pass
class NoSuchCommand(ClientError): pass
#class InvalidCommand(RRDException): pass

def requireExists(f):
    @functools.wraps(f)
    def g(self, *args, **kwargs):
        if not self.db:
            return SeriesNotFound(self.series)
        return f(self, *args, **kwargs)
    return g

def requireNotExists(f):
    @functools.wraps(f)
    def g(self, *args, **kwargs):
        if self.db:
            return SeriesAlreadyExists(self.series)
        return f(self, *args, **kwargs)
    return g

def with_db(method):
    @functools.wraps(method)
    def f(self, slug, *args, **kwargs):
        with self.main_lock:
            lock = self.locks[slug]
            if slug not in self.databases:
                tsdb_filename, _ = self.get_filenames()
                self.databases[slug] = TimeSeriesDatabase(tsdb_filename)
            db = self.databases[slug]
        with lock:
            return method(self, db, *args, **kwargs)
    return f

class DatabaseManager(processing.managers.BaseManager):
    get_client = processing.managers.CreatorMethod(typeid='get_client')

class DatabaseThread(threading.Thread):
    def __init__(self, bail):
        self._bail = bail
        super(DatabaseThread, self).__init__()

    def run(self):
        self.databases = {}
        self.main_lock = threading.Lock()
        self.locks = collections.defaultdict(threading.Lock)

        for path in ('tsdb', 'csv'):
            path = os.path.join(settings.TIME_SERIES_PATH, path)
            if not os.path.exists(path):
                os.makedirs(path)

        def get_client_func():
            return _DatabaseClient(settings.TIME_SERIES_PATH, self.databases, self.main_lock, self.locks)

        class DatabaseManager(processing.managers.BaseManager):
            get_client = processing.managers.CreatorMethod(get_client_func, typeid='get_client')

        self.manager = DatabaseManager(**settings.TIME_SERIES_SERVER_ARGS)

        #self.bail_thread = threading.Thread(target=self.bail_watcher)
        #self.bail_thread.start()

        self.manager.start()

        self._bail.wait()
        self.manager.shutdown()

class _DatabaseClient(object):
    def __init__(self, path, databases, main_lock, locks):
        self.path = path
        self.databases = databases
        self.main_lock = main_lock
        self.locks = locks

    def get_filenames(self, slug):
        return (os.path.join(self.path, 'tsdb', slug + '.tsdb'),
                os.path.join(self.path, 'csv', slug + '.csv'))

    def create(self, slug, series_type, start, interval, archives, timezone_name):
        with self.main_lock:
            lock = self.locks[slug]
        with lock:
            tsdb_filename, csv_filename = self.get_filenames(slug)
            if os.path.exists(tsdb_filename):
                raise SeriesAlreadyExists
            db = TimeSeriesDatabase.create(tsdb_filename, series_type, start, interval, archives, timezone_name)
            with open(csv_filename, 'w') as f:
                pass
            with self.main_lock:
                self.databases[slug] = db

    @with_db
    def get_config(self, db):
        archives = []
        for archive in db.archives:
            archives.append(dict((k, archive[k]) for k in ('aggregation_type', 'aggregation', 'count')))
        return {'start': db.start,
                'interval': db.interval,
                'series_type': db.series_type,
                'timezone_name': db.timezone_name,
                'archives': archives}


class RequestHandler(threading.Thread):
    SERIES_RE = re.compile(r'^[a-zA-Z\d_:.-]{1,64}$')

    def __init__(self, databases, lock, locks, request, client):
        self.databases = databases
        self.main_lock = lock
        self.filename = request[0]
        self.locks = locks
        self.request = request
        self.client = client
        super(RequestHandler, self).__init__()

    def run(self):
        if self.request[1] is not None:
            with self.main_lock:
                self.lock = self.locks[self.request[1]]
        else:
            self.lock = threading.Lock()

        with self.lock:
            data = self.process()
        self.client.send(data)

    def process(self):
        if len(self.request) != 4:
            return ClientError('Request did not have four arguments')
        command, series, args, kwargs = self.request
        if not (isinstance(command, basestring) and \
                (series is None or isinstance(series, basestring)) and \
                isinstance(args, tuple) and \
                isinstance(kwargs, dict)):
            return ClientError("Arguments of wrong type")

        self.series = series
        if self.series:
            if not self.SERIES_RE.match(series):
                return SeriesNotFound("The series name is invalid: %r" % series)
            self.filename = os.path.join(settings.TIME_SERIES_PATH, series.encode('utf-8') + '.tsdb')
        else:
            self.filename = None

        if self.filename and os.path.exists(self.filename):
            with self.main_lock:
                if series in self.databases:
                    self.db = self.databases[series]
                else:
                    self.db = TimeSeriesDatabase(self.filename)
                    self.databases[series] = self.db
        else:
            self.db = None

        try:
            processor = getattr(self, 'process_%s' % command)
        except AttributeError:
            return NoSuchCommand(command)

        try:
            return processor(*args, **kwargs)
        except TimeSeriesException, e:
            return e
        except Exception, e:
            logger.exception("Unexpected exception raised by processor.")
            return TimeSeriesException(e)

    @requireNotExists
    def process_create(self, series_type, start, interval, archives, timezone_name=None):
        db = TimeSeriesDatabase.create(self.filename, series_type, start, interval, archives, timezone_name)
        with self.main_lock:
            self.databases[self.series] = db

    @requireExists
    def process_fetch(self, aggregation_type, interval=None, start=None, end=None):
        if interval is None:
            interval = self.db.interval
        data = self.db.fetch(aggregation_type, interval, start, end)
        return list(data)

    @requireExists
    def process_info(self):
        return self.db.info()

    def process_exists(self):
        return os.path.exists(self.filename)

    @requireExists
    def process_delete(self):
        self.db.close()
        with self.main_lock:
            del self.databases[self.series]
            del self.locks[self.series]
        os.unlink(self.filename)

    @requireExists
    def process_update(self, data):
        self.db.update(data)

    def process_list(self):
        return [fn[:-5] for fn in os.listdir(settings.TIME_SERIES_PATH) if fn.endswith('.tsdb')]

def get_client():
    manager = DatabaseManager.from_address(**settings.TIME_SERIES_SERVER_ARGS)
    return manager.get_client()

def run():
    bail = threading.Event()
    database_thread = DatabaseThread(bail)
    database_thread.start()

    try:
        while database_thread.isAlive():
            time.sleep(1)
    except KeyboardInterrupt:
        bail.set()

if __name__ == '__main__':
    logging.basicConfig(stream=sys.stderr, level=logging.DEBUG)
    run()
