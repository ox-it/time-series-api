import datetime
import math
import os
import pprint
import random
import tempfile

import mock
import unittest2

import pytz

from .base import TimeSeriesDatabase, _from_timestamp, _to_timestamp, isnan

class TimeSeriesDatabaseTestCase(unittest2.TestCase):
    _create_kwargs = {'series_type': 'period',
                      'start': pytz.utc.localize(datetime.datetime.utcnow()).replace(minute=0, second=0, microsecond=0) - datetime.timedelta(0, 1800),
                      'interval': 1800,
                      'archives': [{'aggregation_type': 'average',
                                    'aggregation': 1,
                                    'count': 1000,
                                    'threshold': 0.5},
                                   {'aggregation_type': 'average',
                                    'aggregation': 100,
                                    'count': 2000,
                                    'threshold': 0.5},
                                   {'aggregation_type': 'max',
                                    'aggregation': 200,
                                    'count': 500,
                                    'threshold': 0.5}]}

    class NullDatabase(TimeSeriesDatabase):
        def __init__(self, **kwargs):
            for key in kwargs:
                setattr(self, '_' + key, kwargs[key])
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

        self.assertEqual(new_value, (0, 0))
        self.assertEqual(data_to_insert, [value])

    def testUpdate(self):
        filename, db = self.createDatabase()
        try:
            data, timestamp = [], self._create_kwargs['start']
            for i in xrange(1500):
                timestamp += datetime.timedelta(0, self._create_kwargs['interval'])
                data.append((timestamp, i))
            db.update(data)

            for archive in db.archives:
                cycles, position = divmod(len(data) // archive['aggregation'], archive['count'])
                self.assertEqual(archive['cycles'], cycles)
                self.assertEqual(archive['position'], position)

            stored_data = list(db.fetch('average', 1800, data[0][0], data[-1][0] + datetime.timedelta(10000)))
            expected_data = data[-len(stored_data):]
            for i, (expected, actual) in enumerate(zip(expected_data, stored_data)):
                self.assertEqual(expected, actual, "Mismatch at index %d" % i)
            self.assertEqual(data[-len(stored_data):][-10:], stored_data[-10:])

        finally:
            os.unlink(filename)

    def testTimestamps(self):
        local = pytz.timezone("Europe/London")
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
            new_tests.append(test.replace(tzinfo=local))
        for test in new_tests:
            self.assertEqual(test,
                             _from_timestamp(_to_timestamp(test)),
                             "Failed to round-trip %r" % test)
            self.assertEqual(_to_timestamp(test), _to_timestamp(_from_timestamp(_to_timestamp(test))))


if __name__ == '__main__':
    unittest2.main()