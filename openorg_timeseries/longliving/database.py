from __future__ import with_statement

import collections
import csv
import functools
import logging
import os
import sys
import threading
import time

import multiprocessing.managers

from django.conf import settings
from openorg_timeseries.database import TimeSeriesDatabase

logger = logging.getLogger(__name__)

class TimeSeriesException(Exception): pass
class ClientError(TimeSeriesException): pass
class SeriesNotFound(ClientError): pass
class SeriesAlreadyExists(ClientError): pass
class NoSuchCommand(ClientError): pass

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

def with_db(method=None, with_csv=False):
    if method is None:
        return functools.partial(with_db, with_csv=with_csv)
    @functools.wraps(method)
    def f(self, slug, *args, **kwargs):
        tsdb_filename, csv_filename = self.get_filenames(slug)
        with self.main_lock:
            lock = self.locks[slug]
            if slug not in self.databases:
                try:
                    self.databases[slug] = TimeSeriesDatabase(tsdb_filename)
                except IOError:
                    raise SeriesNotFound
            db = self.databases[slug]

        with lock:
            if with_csv:
                with open(csv_filename, 'a+b') as csv_file:
                    csv_writer = csv.writer(csv_file)
                    return method(self, db, csv_writer, *args, **kwargs)
            else:
                return method(self, db, *args, **kwargs)
    return f


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

        self.manager = multiprocessing.managers.BaseManager(**settings.TIME_SERIES_SERVER_ARGS)
        self.manager.register('get_client', get_client_func)

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

    def delete(self, slug):
        with self.main_lock:
            lock = self.locks[slug]
            with lock:
                if slug in self.databases:
                    self.databases.pop(slug).close()
                for filename in self.get_filenames(slug):
                    os.unlink(filename)

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

    @with_db(with_csv=True)
    def append(self, db, csv_writer, readings):
        last, tz = db.last, db.timezone
        readings = sorted((r[0].astimezone(tz), float(r[1])) for r in readings if r[0] > last)
        for ts, val in readings:
            csv_writer.writerow([ts.isoformat('T'), val])
        db.update(readings)
        return {'appended': len(readings),
                'last': db.last}

    @with_db
    def fetch(self, db, aggregation_type, interval, period_start, period_end):
        return list(db.fetch(aggregation_type, interval, period_start, period_end))

def get_client():
    manager = multiprocessing.managers.BaseManager(**settings.TIME_SERIES_SERVER_ARGS)
    manager.connect()
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
