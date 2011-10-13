from __future__ import with_statement

import collections
import contextlib
import datetime
import functools
import logging
import os
import re
import select
import subprocess
import sys
import threading
import time

import processing.connection

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

class DatabaseThread(threading.Thread):
    def __init__(self, bail):
        self._bail = bail
        super(DatabaseThread, self).__init__()

    def run(self):
        self.databases = {}
        self.lock = threading.Lock()
        self.locks = collections.defaultdict(threading.Lock)

        listener = processing.connection.Listener(('localhost', settings.TIME_SERIES_PORT))

        rlist = [listener._listener._socket]

        while not self._bail.isSet():
            rlist_ready, _, _ = select.select(rlist, (), (), 1)
            if not rlist_ready:
                continue

            for obj in rlist_ready:
                if obj is listener._listener._socket:
                    conn = listener.accept()
                    logger.info("Received connection from %r", conn)
                    rlist.append(conn)
                else:
                    try:
                        request = obj.recv()
                    except EOFError:
                        rlist.remove(obj)
                    else:
                        rh = RequestHandler(self.databases, self.lock, self.locks, request, obj)
                        rh.run()

        for obj in rlist:
            obj.close()

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
            self.client.send(self.process())

    def process(self):
        if len(self.request) != 4:
            return ClientError
        command, series, args, kwargs = self.request
        if not (isinstance(command, basestring) and \
                (series is None or isinstance(series, basestring)) and \
                isinstance(args, tuple) and \
                isinstance(kwargs, dict)):
            return ClientError

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
            return NoSuchCommand

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

class DatabaseClient(object):
    def __init__(self):
        self.client = processing.connection.Client(('localhost', settings.TIME_SERIES_PORT))

    def command(self, command, series, *args, **kwargs):
        self.client.send((command, series, args, kwargs))
        value = self.client.recv()
        if isinstance(value, TimeSeriesException):
            raise value
        else:
            return value

    def fetch(self, series, aggregation_type, interval=None, start=None, end=None):
        return self.command('fetch', series, aggregation_type, interval, start, end)

    def info(self, series):
        return self.command('info', series)

    def exists(self, series):
        return self.command('exists', series)

    def delete(self, series):
        return self.command('delete', series)

    def create(self, series, series_type, start, interval, archives, timezone_name=None):
        return self.command('create', series, series_type, start, interval, archives, timezone_name)

    def update(self, series, data):
        return self.command('update', series, data)

    def list(self):
        return self.command('list', None)

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
