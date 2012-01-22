from __future__ import division

import calendar
import copy
import datetime
import logging
import math
import mmap
import os
import struct

import pytz

try:
    isnan = math.isnan
except AttributeError:
    def isnan(value):
        return isinstance(value, float) and value != value

def _to_timestamp(dt):
    return calendar.timegm(dt.astimezone(pytz.utc).timetuple())

def _from_timestamp(ts):
    return pytz.utc.localize(datetime.datetime.utcfromtimestamp(ts))

logger = logging.getLogger(__name__)

class TimeSeriesDatabase(object):
    _series_types = dict(enumerate('period gauge counter'.split()))
    _series_types_inv = dict((v, k) for k, v in _series_types.items())
    _aggregation_types = dict(enumerate('average min max'.split()))
    _aggregation_types_inv = dict((v, k) for k, v in _aggregation_types.items())

    _value_format = '<f'
    _value_format_size = struct.calcsize(_value_format)

    _header_format = '<qqLL64sq'
    _header_format_size = struct.calcsize(_header_format)

    # For recording the most recent timestamp
    _last_format = '<L'
    _last_offset = struct.calcsize(_header_format[:-1])

    _archive_meta_format = '<LLLLLfff'
    _archive_meta_format_size = struct.calcsize(_archive_meta_format)

    def __init__(self, filename):
        self.filename = filename
        if not os.path.exists(filename):
            raise IOError("There is no time-series database to be found at %r" % filename)

        f = open(filename, 'r+b')
        self._map = mmap.mmap(f.fileno(), 0)

        series_type, start, self._interval, archive_count, timezone_name, last = self._read(self._header_format)
        self._timezone_name = timezone_name.rstrip('\0')
        self._timezone = pytz.timezone(self._timezone_name)
        self._series_type = self._series_types[series_type]
        self._start = _from_timestamp(start)
        self._last = _from_timestamp(last)

        self._archives = []
        for i in range(archive_count):
            aggregation_type, aggregation, count, cycles, position, threshold, state_a, state_b = self._read(self._archive_meta_format)
            archive = {'aggregation_type': self._aggregation_types[aggregation_type],
                       'aggregation': aggregation,
                       'count': count,
                       'cycles': cycles,
                       'position': position,
                       'threshold': threshold,
                       'state': (state_a, state_b)}
            self._archives.append(archive)
        pos = self._map.tell()
        for archive in self._archives:
            archive['offset'] = pos
            pos += archive['count'] * self._value_format_size


    def _read(self, fmt, pos=None, whence=None):
        if pos is not None:
            self._map.seek(pos, whence)
        data = self._map.read(struct.calcsize(fmt))
        result = struct.unpack(fmt, data)
        if len(result) == 1:
            return result[0]
        return result

    def _write(self, fmt, data, pos=None, whence=None):
        if pos is not None:
            self._map.seek(pos, whence)
        if not isinstance(data, tuple):
            data = (data,)
        self._map.write(struct.pack(fmt, *data))

    @classmethod
    def create(cls, filename, series_type, start, interval, archives, timezone_name):
        assert series_type in cls._series_types_inv
        assert isinstance(start, datetime.datetime)
        assert start.tzinfo is not None
        assert isinstance(interval, int)
        assert isinstance(archives, list)

        start_timestamp = _to_timestamp(start)
        start_timestamp -= (-start_timestamp) % interval

        timezone_name = timezone_name or 'UTC'
        if isinstance(timezone_name, unicode):
            timezone_name = timezone_name.encode('utf-8')
        if timezone_name not in pytz.all_timezones:
            raise ValueError("Timezone not recognized.")
        if len(timezone_name) > 63:
            raise ValueError("Timezone specifier too long.")

        f = open(filename, 'wb')
        f.write(struct.pack(cls._header_format,
                            cls._series_types_inv[series_type],
                            start_timestamp,
                            interval,
                            len(archives),
                            timezone_name,
                            start_timestamp))

        for archive in archives:
            archive['threshold'] = archive.get('threshold') or 0.5
            f.write(struct.pack(cls._archive_meta_format,
                                cls._aggregation_types_inv[archive['aggregation_type']],
                                archive['aggregation'],
                                archive['count'],
                                0,
                                0,
                                archive['threshold'],
                                float('nan'),
                                float('nan')))

        pos = f.tell()
        zeros = struct.pack(cls._value_format, float('nan')) * 1024
        for archive in archives:
            archive['offset'] = pos
            pos += archive['count'] * cls._value_format_size
            for i in xrange(0, archive['count'], 1024):
                zero_count = min(archive['count'] - i, 1024)
                if zero_count == 1024:
                    f.write(zeros)
                else:
                    f.write(struct.pack(cls._value_format, float('nan')) * zero_count)

        f.close()
        return cls(filename)

    def update(self, data):
        if not data:
            return
        for archive in self._archives:
            self._update_archive(archive, data)
        self._sync_archive_meta()
        self._sync_last_timestamp(data[-1][0])

    def _update_archive(self, archive, data):
        last_timestamp, state = self._last, archive['state']
        data_to_insert = []
        for i, (timestamp, value) in enumerate(data):
            if timestamp <= last_timestamp:
                logger.warning("Datum with timestamp '%s' ignored (should be after '%s')" % (timestamp, last_timestamp))
                continue
            state, new_data_to_insert = self._combine(archive, last_timestamp, state, timestamp, value)
            data_to_insert.extend(new_data_to_insert)
            last_timestamp = timestamp
        archive['state'] = state

        self._insert_data(archive, data_to_insert)

    def _insert_data(self, archive, data):
        last_timestamp, state = self._last, archive['state']

        self._map.seek(archive['offset'] + archive['position'] * self._value_format_size)
        for datum in data:
            self._write(self._value_format, datum)
            archive['position'] += 1
            if archive['position'] == archive['count']:
                archive['position'] = 0
                archive['cycles'] += 1
                self._map.seek(archive['offset'])

    def _combine(self, archive, old_timestamp, state, timestamp, value):
        ots, ts = old_timestamp, timestamp
        old_timestamp, timestamp = _to_timestamp(old_timestamp), _to_timestamp(timestamp)
        interval = self._interval * archive['aggregation']

        intermediates, intermediate = [], math.ceil(old_timestamp / interval) * interval
        while intermediate <= timestamp:
            if intermediate > old_timestamp:
                intermediates.append(intermediate)
            intermediate += interval

        data_to_insert = []
        if self._series_type == 'period':
            default_state = {'average': 0,
                             'min': float('inf'),
                             'max': float('-inf')}.get(archive['aggregation_type'])
            combine_function = {'average': lambda state, value: state + value * period / self.interval / archive['aggregation'],
                                'min': min,
                                'max': max}.get(archive['aggregation_type'])
            state_value, state_count = state

            if isnan(state_value):
                state_value, state_count = default_state, 0

            state_count += 1

            last_intermediate = old_timestamp
            for intermediate in intermediates:
                period = intermediate - last_intermediate
                if archive['aggregation_type'] == 'average' and (state_value < 0 or value < 0):
                    raise Exception
                if state_count / archive['aggregation'] >= archive['threshold']:
                    data_to_insert.append(combine_function(state_value, value))
                else:
                    data_to_insert.append(float('nan'))
                last_intermediate, state_value, state_count = intermediate, default_state, 0

            period = timestamp - last_intermediate
            state_value = combine_function(state_value, value)
            state = state_value, state_count

        elif self._series_type == 'gauge':
            state_value, _ = state
            if isnan(state_value):
                state_value = value
            last_intermediate = old_timestamp
            for intermediate in intermediates:
                data_to_insert.append(state_value + (value - state_value) * (timestamp - intermediate) / (timestamp - old_timestamp))
            state = state_value, _
        elif self._series_type == 'counter':
            state_value, _ = state
            if isnan(state_value):
                state_value = value
            else:
                for intermediate in intermediates:
                    data_to_insert.append()
            state = state_value, _

        return state, data_to_insert

    def fetch(self, aggregation_type, interval, period_start, period_end):
        if not period_end:
            period_end = pytz.utc.localize(datetime.datetime.utcnow())
        if not period_start:
            period_start = period_end - datetime.timedelta(2)
        period_start = max(period_start, self._start)

        for archive in self._archives:
            if archive['aggregation_type'] == aggregation_type and archive['aggregation'] * self._interval == interval:
                break
        else:
            raise ValueError("No suitable archive")

        period_start, period_end = map(_to_timestamp, [period_start, period_end])
        period_start = math.ceil(period_start / interval) * interval
        period_end = math.floor(period_end / interval) * interval

        offset_start = int(period_start - _to_timestamp(self._start)) // interval
        offset_end = int(period_end - _to_timestamp(self._start)) // interval
        offset_end = min(offset_end, archive['cycles'] * archive['count'] + archive['position']) - 1

        seek_to = max(offset_start, (archive['cycles'] - 1) * archive['count'] + archive['position'])
        self._map.seek(archive['offset'] + (seek_to % archive['count']) * self._value_format_size)


        timestamp = _to_timestamp(self._start) + seek_to * self._interval * archive['aggregation']
        for i in xrange(seek_to, offset_end + 1):
            if i % archive['count'] == 0:
                self._map.seek(archive['offset'])
            timestamp += self._interval * archive['aggregation']
            yield (_from_timestamp(timestamp).astimezone(self.timezone),
                   self._read(self._value_format))

    def info(self):
        result = {
            'updated': self._last,
            'interval': self._interval,
            'type': self._series_type,
            'samples': []
        }
        for archive in self._archives:
            result['samples'].append({'type': archive['aggregation_type'],
                                      'resolution': archive['aggregation'] * self._interval,
                                      'count': archive['count'],
                                      'aggregation': archive['aggregation']})
        return result

    def _sync_archive_meta(self):
        self._map.seek(self._header_format_size)
        for archive in self._archives:
            self._write(self._archive_meta_format,
                        (self._aggregation_types_inv[archive['aggregation_type']],
                         archive['aggregation'],
                         archive['count'],
                         archive['cycles'],
                         archive['position'],
                         archive['threshold'],
                         archive['state'][0],
                         archive['state'][1]))

    def _sync_last_timestamp(self, last):
        self._last = last
        self._map.seek(self._last_offset)
        self._map.write(struct.pack(self._last_format,
                                    _to_timestamp(self._last)))

    def flush(self):
        self._map.flush()
    def close(self):
        self._map.close()

    series_type = property(lambda self: self._series_type)
    start = property(lambda self: self._start)
    interval = property(lambda self: self._interval)
    archives = property(lambda self: copy.deepcopy(self._archives))
    timezone = property(lambda self: self._timezone)
    timezone_name = property(lambda self: self._timezone_name)
    last = property(lambda self: self._last)
