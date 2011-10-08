from __future__ import division

import copy
import datetime
import math
import mmap
import os
import struct
import time

import pytz

try:
    isnan = math.isnan
except AttributeError:
    def isnan(value):
        return isinstance(value, float) and value != value

def _to_timestamp(dt):
    return time.mktime(dt.astimezone(pytz.utc).timetuple())
def _from_timestamp(ts):
    return pytz.utc.localize(datetime.datetime.utcfromtimestamp(ts))

class TimeSeriesDatabase(object):
    _series_types = dict(enumerate('period gauge counter'.split()))
    _series_types_inv = dict((v, k) for k, v in _series_types.items())
    _aggregation_types = dict(enumerate('average min max'.split()))
    _aggregation_types_inv = dict((v, k) for k, v in _aggregation_types.items())

    _value_format = '<f'
    _value_format_size = struct.calcsize(_value_format)

    _header_format = '<qLLL'
    _header_format_size = struct.calcsize(_header_format)
    _archive_meta_format = '<LLLLLLf'
    _archive_meta_format_size = struct.calcsize(_archive_meta_format)

    def __init__(self, filename):
        self.filename = filename
        if os.path.exists(filename):
            f = open(filename, 'r+b')
            self._map = mmap.mmap(f.fileno(), 0)

            series_type, start, self._interval, archive_count = self._read(self._header_format)
            self._series_type = self._series_types[series_type]
            self._start = _from_timestamp(start)

            self._archives = []
            for i in range(archive_count):
                aggregation_type, aggregation, count, cycles, position, last_timestamp, last_value = self._read(self._archive_meta_format)
                archive = {'aggregation_type': self._aggregation_types[aggregation_type],
                           'aggregation': aggregation,
                           'count': count,
                           'cycles': cycles,
                           'position': position,
                           'last_timestamp': _from_timestamp(last_timestamp),
                           'last_value': last_value}
                self._archives.append(archive)
            pos = self._map.tell()
            for archive in self._archives:
                archive['offset'] = pos
                pos += archive['count'] * self._value_format_size

        else:
            self._map = None

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
    def create(cls, filename, series_type, start, interval, archives):
        assert start.tzinfo is not None
        start_timestamp = _to_timestamp(start)
        start_timestamp -= start_timestamp % interval

        f = open(filename, 'wb')
        f.write(struct.pack(cls._header_format,
                            cls._series_types_inv[series_type],
                            start_timestamp,
                            interval,
                            len(archives)))

        for archive in archives:
            f.write(struct.pack(cls._archive_meta_format,
                                cls._aggregation_types_inv[archive['aggregation_type']],
                                archive['aggregation'],
                                archive['count'],
                                0,
                                0,
                                start_timestamp,
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
        for archive in self._archives:
            self._update_archive(archive, data)
        self._sync_archive_meta()

    def _update_archive(self, archive, data):
        last_timestamp, last_value = archive['last_timestamp'], archive['last_value']
        data_to_insert = []
        for i, (timestamp, value) in enumerate(data):
            last_value, new_data_to_insert = self._combine(archive, last_timestamp, last_value, timestamp, value)
            data_to_insert.extend(new_data_to_insert)
            last_timestamp = timestamp

        if len(data) == len(data_to_insert):
            assert [datum[1] for datum in data] == data_to_insert

        self._insert_data(archive, data_to_insert)

    def _insert_data(self, archive, data):
        last_timestamp, last_value = archive['last_timestamp'], archive['last_value']

        self._map.seek(archive['offset'] + archive['position'] * self._value_format_size)
        for datum in data:
            self._write(self._value_format, datum)
            archive['position'] += 1
            if archive['position'] == archive['count']:
                archive['position'] = 0
                archive['cycles'] += 1
                self._map.seek(archive['offset'])
        archive['last_timestamp'] = last_timestamp + datetime.timedelta(0, archive['aggregation'] * self._interval * len(data))
        archive['last_value'] = last_value

    def _combine(self, archive, old_timestamp, old_value, timestamp, value):
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
            if isnan(old_value):
                old_value = 0
            last_intermediate = old_timestamp
            for intermediate in intermediates:
                period = intermediate - last_intermediate
                intermediate_value, old_value = old_value + period * value, 0
                data_to_insert.append(intermediate_value / period / archive['aggregation'])
                last_intermediate = intermediate
            new_value = old_value + (timestamp - last_intermediate) * value
        elif self._series_type == 'gauge':
            if isnan(old_value):
                old_value = value
            last_intermediate = old_timestamp
            for intermediate in intermediates:
                data_to_insert(old_value + (value - old_value) * (timestamp - intermediate) / (timestamp - old_timestamp))
            old_value = value
        elif self._series_type == 'counter':
            if isnan(old_value):
                return value, []
            for intermediate in intermediates:
                data_to_insert.append()
            new_value = value

        return new_value, data_to_insert

    def fetch(self, aggregation_type, interval, period_start, period_end):
        for archive in self._archives:
            if archive['aggregation_type'] == aggregation_type and archive['aggregation'] * self._interval == interval:
                break
        else:
            raise ValueError("No suitable archive")

        print
        print period_start, period_end
        period_start, period_end = map(_to_timestamp, [period_start, period_end])
        period_start = math.ceil(period_start / interval) * interval
        period_end = math.floor(period_end / interval) * interval

        offset_start = int(period_start - _to_timestamp(self._start)) // interval
        offset_end = int(period_end - _to_timestamp(self._start)) // interval

        seek_to = max(offset_start, (archive['cycles'] - 1) * archive['count'] + archive['position'])
        print "SEEK", seek_to
        self._map.seek(archive['offset'] + (seek_to % archive['count']) * self._value_format_size)


        timestamp = _to_timestamp(self._start) + offset_start * self._interval * archive['aggregation']
        for i in xrange(offset_start, offset_end + 1):
            if i <= seek_to:
                yield _from_timestamp(timestamp), float('nan')
            else:
                yield _from_timestamp(timestamp), self._read(self._value_format)
            if i % archive['count'] == 0:
                self._map.seek(archive['offset'])
            timestamp += self._interval * archive['aggregation']

    def _sync_archive_meta(self):
        self._map.seek(self._header_format_size)
        for archive in self._archives:
            self._write(self._archive_meta_format,
                        (self._aggregation_types_inv[archive['aggregation_type']],
                         archive['aggregation'],
                         archive['count'],
                         archive['cycles'],
                         archive['position'],
                         _to_timestamp(archive['last_timestamp']),
                         archive['last_value']))


    series_type = property(lambda self: self._series_type)
    start = property(lambda self: self._start)
    interval = property(lambda self: self._interval)
    archives = property(lambda self: copy.deepcopy(self._archives))
