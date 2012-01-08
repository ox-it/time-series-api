import datetime
import math
import operator
import os
import random
import tempfile
import time

import mock
import unittest2

import pytz

from .base import TimeSeriesDatabase, _from_timestamp, _to_timestamp, isnan

class TimeSeriesDatabaseTestCase(unittest2.TestCase):
    _create_kwargs = {'series_type': 'period',
                      'interval': 1800,
                      'archives': [{'aggregation_type': 'average',
                                    'aggregation': 1,
                                    'count': 1000,
                                    'threshold': 0.5},
                                   {'aggregation_type': 'min',
                                    'aggregation': 20,
                                    'count': 2000,
                                    'threshold': 0.5},
                                   {'aggregation_type': 'max',
                                    'aggregation': 50,
                                    'count': 500,
                                    'threshold': 0.5}],
                      'timezone_name': 'Europe/London'}

    # Find a start date as a multiple of all our aggregations, so that aggregating archives
    # line up properly.
    _create_start = time.time()
    _create_start -= _create_start % (_create_kwargs['interval'] * reduce(operator.mul, [a['aggregation'] for a in _create_kwargs['archives']]))
    _create_kwargs['start'] = _from_timestamp(_create_start)


    class NullDatabase(TimeSeriesDatabase):
        def __init__(self, **kwargs):
            for key in kwargs:
                setattr(self, '_' + key, kwargs[key])
            self._timezone = pytz.timezone(kwargs['timezone_name'])
            self._map = mock.Mock()

    def createDatabase(self):
        fd, filename = tempfile.mkstemp()
        os.close(fd)
        try:
            return filename, TimeSeriesDatabase.create(filename, **self._create_kwargs)
        except Exception:
            os.unlink(filename)
            raise

    def testCreate(self):
        filename, db = self.createDatabase()
        try:
            self.assertEqual(db.start, self._create_kwargs['start'])
            self.assertEqual(db.interval, self._create_kwargs['interval'])
            for expected, actual in zip(self._create_kwargs['archives'], db.archives):
                actual = dict((k, actual[k]) for k in expected)
                self.assertEqual(expected, actual)
        finally:
            os.unlink(filename)

    def testCombineAverage(self):
        db = self.NullDatabase(**self._create_kwargs)

        old_timestamp = datetime.datetime(2011, 1, 1, 12, 0, 0, tzinfo=pytz.utc)
        timestamp = datetime.datetime(2011, 1, 1, 12, 30, 0, tzinfo=pytz.utc)
        state = float('nan'), float('nan')
        value = 300

        new_value, data_to_insert = db._combine(db.archives[0], old_timestamp, state, timestamp, value)

        self.assertEqual(new_value[0], 0)
        self.assertEqual(data_to_insert, [value])

    def testUpdate(self):
        filename, db = self.createDatabase()
        try:
            data, timestamp = [], self._create_kwargs['start']
            for i in xrange(1500):
                timestamp += datetime.timedelta(0, self._create_kwargs['interval'])
                data.append((timestamp, i))
            db.update(data)

            for i, archive in enumerate(db.archives):
                cycles, position = divmod(len(data) // archive['aggregation'], archive['count'])
                self.assertEqual(archive['cycles'], cycles, "Archive %d (%d/%d)" % (i, cycles, position))
                self.assertEqual(archive['position'], position, "Archive %d (%d/%d)" % (i, cycles, position))

            stored_data = list(db.fetch('average', 1800, data[0][0], data[-1][0] + datetime.timedelta(10000)))
            expected_data = data[-len(stored_data):]
            for i, (expected, actual) in enumerate(zip(expected_data, stored_data)):
                self.assertEqual(expected, actual, "Mismatch at index %d" % i)
            self.assertEqual(data[-len(stored_data):][-10:], stored_data[-10:])

        finally:
            os.unlink(filename)

    def testUpdateEmpty(self):
        filename, db = self.createDatabase()
        db.update([])

    def testTimestamps(self):
        local1 = pytz.timezone("Europe/London")
        local2 = pytz.timezone("America/New_York")
        tests = [datetime.datetime(2011, 1, 1, 0, 0),
                 datetime.datetime(2011, 7, 1, 0, 0),
                 datetime.datetime(2011, 3, 27, 1, 0),
                 datetime.datetime(2011, 3, 27, 2, 0),
                 datetime.datetime(2011, 10, 30, 1, 0),
                 datetime.datetime(2011, 10, 30, 1, 30),
                 datetime.datetime(2011, 10, 30, 2, 0)]
        new_tests = []
        for test in tests:
            new_tests.append(test.replace(tzinfo=pytz.utc))
            new_tests.append(test.replace(tzinfo=local1))
            new_tests.append(test.replace(tzinfo=local2))
        for test in new_tests:
            self.assertEqual(test,
                             _from_timestamp(_to_timestamp(test)),
                             "Failed to round-trip %r (%r) -> %r -> %r" % (test,
                                                                           test.astimezone(pytz.utc).timetuple(),
                                                                      _to_timestamp(test),
                                                                      _from_timestamp(_to_timestamp(test))))
            self.assertEqual(_to_timestamp(test), _to_timestamp(_from_timestamp(_to_timestamp(test))))

    def testWithGap(self):
        """
        Make sure aggregation works when we have gaps
        """
        filename, db = self.createDatabase()
        try:
            data, timestamp = [], db.start
            for i in xrange(100):
                timestamp += datetime.timedelta(0, random.randrange(1, 5000))
                data.append((timestamp, random.randrange(0, 100)))
            db.update(data)

            for archive in db.archives:
                stored_data = list(db.fetch(archive['aggregation_type'],
                                            archive['aggregation'] * db.interval,
                                            db.start,
                                            timestamp))
                # The following lines are useful for debugging a failing test
                #print
                #print '=' * 80
                #print '\n'.join('%s,%s' % (ts.strftime('%Y-%m-%d %H:%M:%S'), unicode(val)) for ts, val in data)
                #print '-' * 80
                #print '\n'.join('%s,%s' % (ts.strftime('%Y-%m-%d %H:%M:%S'), unicode(val)) for ts, val in stored_data)
                #print '=' * 80
                for ts, val in stored_data:
                    if not isnan(val):
                        self.assert_(0 <= val <= 100, "%s is unexpectedly out of range" % val)
        finally:
            os.unlink(filename)

    def testBatchUpdate(self):
        """
        There was at one point a bug where updating in small groups lead to doubled values
        at the boundaries. Here we'll create one database in one go, and create the other
        in batches, before checking that they're identical.
        """
        filename_once, db_once = self.createDatabase()
        filename_batch, db_batch = self.createDatabase()
        try:
            data, timestamp = [], db_once.start
            for i in xrange(200):
                timestamp += datetime.timedelta(0, random.randrange(1, 1800))
                data.append((timestamp, random.randrange(0, 100)))

            db_once.update(data)
            for i in xrange(0, len(data), 5):
                # Intentionally overlap
                db_batch.update(data[i:i + 10])

            for i, archive in enumerate(db_once.archives):
                data_once = list(db_once.fetch(archive['aggregation_type'],
                                               archive['aggregation'] * db_once.interval,
                                               db_once.start,
                                               timestamp))
                data_batch = list(db_batch.fetch(archive['aggregation_type'],
                                                 archive['aggregation'] * db_batch.interval,
                                                 db_batch.start,
                                                 timestamp))
                self.assertEqual(data_once, data_batch, "Archive %d" % i)


        finally:
            os.unlink(filename_once)
            os.unlink(filename_batch)

if __name__ == '__main__':
    unittest2.main()
