import collections
import datetime
import logging
import os
import re
import select
import subprocess
import threading
import time

import processing.connection

from django.conf import settings

class RRDException(Exception): pass
class ClientError(RRDException): pass
class NoSuchCommand(ClientError): pass
class SeriesNotFound(ClientError): pass
class SeriesAlreadyExists(ClientError): pass
class CFNotAvailable(ClientError): pass
class InvalidCommand(RRDException): pass
class RRDToolError(RRDException): pass
class UnexpectedRRDException(RRDException): pass

logger = logging.getLogger(__name__)

class RRDThread(threading.Thread):
    SERIES_RE = re.compile(r'^[a-zA-Z\d_:.-]{1,64}$')

    def __init__(self, bail):
        self._bail = bail
        super(RRDThread, self).__init__()

    def run(self):
        self.rrdtool = subprocess.Popen(['rrdtool', '-'], stdin=subprocess.PIPE, stdout=subprocess.PIPE)

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
                        obj.send(self.process(request))

        for obj in rlist:
            obj.close()
            
        self.write('quit\n')
        self.rrdtool.wait()
        
    def write(self, data):
        logger.debug("> %r", data)
        self.rrdtool.stdin.write(data)

    def consume(self, it):
        for _ in it:
            pass

    def get_lines(self, ignore_error=False):
        while True:
            line = self.rrdtool.stdout.readline()
            logger.debug('< %r', line)
            line = line.strip()
            if line.startswith('OK '):
                break
            if line.startswith('ERROR: '):
                raise RRDToolError(line)
            if not ignore_error and line == 'RRDtool 1.4.4  Copyright 1997-2010 by Tobias Oetiker <tobi@oetiker.ch>':
                self.consume(self.get_lines())
                raise InvalidCommand
            yield line

    def process(self, request):
        if len(request) != 4:
            return ClientError
        command, series, args, kwargs = request
        if not (isinstance(command, basestring) and isinstance(series, basestring) \
            and isinstance(args, tuple) and isinstance(kwargs, dict)):
            return ClientError
        
        #logger.info("Received command: %r %r, %r, %r", command, series, args, kwargs)

        filename = os.path.join(settings.TIME_SERIES_PATH, series.encode('utf-8') + '.rrd')
        series_exists = os.path.exists(filename)

        if command != 'list' and not self.SERIES_RE.match(series):
            return SeriesNotFound("The series name is invalid: %r" % series)
        if command == 'list':
            pass
        elif command == 'create':
            if series_exists:
                return SeriesAlreadyExists("A series with that name already exists.")
        elif command == 'exists':
            pass
        elif not series_exists:
            return SeriesNotFound("There is no such series.")

        try:
            processor = getattr(self, 'process_%s' % command)
        except AttributeError:
            return NoSuchCommand

        try:
            return processor(filename, *args, **kwargs)
        except RRDException, e:
            return e
        except Exception, e:
            logger.exception("Unexpected exception raised by processor.")
            return UnexpectedRRDException(e)

    def process_fetch(self, filename, aggregation_type, start=None, end=None, resolution=None):
        command = ['fetch', filename, aggregation_type.upper()]
        if start:
            if resolution:
                start = (start // resolution) * resolution
            command.extend(('-s', str(start)))
        if end:
            if resolution:
                end = (end // resolution) * resolution
            command.extend(('-e', str(end)))
        if resolution:
            command.extend(('-r', str(resolution)))

        self.write(' '.join(command) + '\n')

        try:
            results = []
            for line in self.get_lines():
                if ': ' not in line:
                    continue
                ts, val = line.split(': ')
                ts, val = int(ts), float(val)
                if val != val: # Catch NaN, and replace with None
                    val = None
                results.append((datetime.datetime.fromtimestamp(ts), val))
        except RRDToolError, e:
            if 'should be less than end' in e.message:
                results = []
            elif e.message.startswith('ERROR: start time: '):
                results = []
            elif e.message == "ERROR: the RRD does not contain an RRA matching the chosen CF":
                raise CFNotAvailable()
            else:
                raise

        return results

    def process_info(self, filename):
        self.write('info %s\n' % filename)

        raw_result = {'ds': collections.defaultdict(dict),
                      'rra': collections.defaultdict(dict)}
        for line in self.get_lines():
            key, value = line.split(" = ", 1)
            try:
                value = float(value)
            except ValueError:
                value = eval(value, {}, {})
            if key.startswith('rra['):
                raw_result['rra'][key[4:key.index(']')]][key[key.index('.')+1:]] = value
            elif key.startswith('ds['):
                raw_result['ds'][key[3:key.index(']')]][key[key.index('.')+1:]] = value
            else:
                raw_result[key] = value
        raw_result['rra'] = [raw_result['rra'][i] for i in sorted(raw_result['rra'])]

        result = {
            'updated': datetime.datetime.fromtimestamp(raw_result['last_update']),
            'interval': int(raw_result['step']),
            'type': raw_result['ds']['val']['type'].lower(),
            'value': raw_result['ds']['val']['value'],
            'samples': [],
        }

        for rra in raw_result['rra']:
            result['samples'].append({
                'type': rra['cf'].lower(),
                'resolution': int(rra['pdp_per_row'] * result['interval']),
                'count': int(rra['rows']),
                'aggregation': int(rra['pdp_per_row']),
            })

        return result

    def process_exists(self, filename):
        return os.path.exists(filename)

    def process_delete(self, filename):
        os.unlink(filename)

    def process_create(self, filename, type, start, step, heartbeat, min, max, rras):
        min = "U" if min is None else min
        max = "U" if max is None else max
        heartbeat = 48 if heartbeat is None else heartbeat

        args = {'filename': filename,
                'start': int(time.mktime(start.timetuple())),
                'step': step,
                'type': type.upper(),
                'heartbeat': heartbeat,
                'min': min,
                'max': max}
        command = ("create %(filename)s --start %(start)s --step %(step)d" \
                 + " DS:val:%(type)s:%(heartbeat)s:%(min)s:%(max)s") % args

        for rra in rras:
            rra['xff'] = rra.get('xff') or 0.5
            rra['type'] = rra['type'].upper()
            command += " RRA:%(type)s:%(xff).2f:%(steps)d:%(rows)d" % rra

        self.write('%s\n' % command)
        self.consume(self.get_lines())

    def process_update(self, filename, data):
        for i in range(0, len(data), 128):
            update_data = data[i:i+128]

            command = ["update", filename]
            for ts, value in update_data:
                ts = time.mktime(ts.timetuple())
                command.append('%d:%f' % (ts, value))
            command = " ".join(command) + '\n'

            self.write(command)
            self.consume(self.get_lines())
    
    def process_list(self, series):
        return [fn[:-4] for fn in os.listdir(settings.TIME_SERIES_PATH) if fn.endswith('.rrd')]

class RRDClient(object):
    def __init__(self):
        self.client = processing.connection.Client(('localhost', settings.TIME_SERIES_PORT))

    def command(self, command, series, *args, **kwargs):
        self.client.send((command, series, args, kwargs))
        value = self.client.recv()
        if isinstance(value, RRDException):
            raise value
        else:
            return value

    def fetch(self, series, aggregation_type, start=None, end=None, resolution=None):
        return self.command('fetch', series, aggregation_type, start, end, resolution)

    def info(self, series):
        return self.command('info', series)

    def exists(self, series):
        return self.command('exists', series)

    def delete(self, series):
        return self.command('delete', series)

    def create(self, series, type, start, step, heartbeat, min, max, rras):
        return self.command('create', series, type, start, step, heartbeat, min, max, rras)

    def update(self, series, data):
        return self.command('update', series, data)
    
    def list(self):
        return self.command('list', '*')

def run():
    bail = threading.Event()
    rrd_thread = RRDThread(bail)
    rrd_thread.start()

    try:
        while rrd_thread.isAlive():
            time.sleep(1)
    except KeyboardInterrupt:
        bail.set()
