from __future__ import with_statement

import csv
import logging
import os
import sys
import threading
import time
import traceback

import dateutil.parser
from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.management import call_command

from openorg_timeseries.longliving.database import DatabaseThread
from openorg_timeseries.models import TimeSeries
import openorg_timeseries.demo

class Command(BaseCommand):
    def handle(self, addrport='', **options):
        if not os.path.exists(settings.DATA_DIR):
            os.makedirs(settings.DATA_DIR)

        logging.basicConfig(stream=sys.stderr, level=logging.INFO)
        call_command('syncdb')
        call_command('collectstatic', interactive=False, link=True)

        bail = threading.Event()
        database_thread = DatabaseThread(bail)
        database_thread.start()

        try:
            time.sleep(1)
            self.load_demo_data()
            call_command('runserver', use_reloader=False)
        except BaseException:
            traceback.print_exc()

        bail.set()
        database_thread.join()

    demo_timeseries = {'slug': 'demo',
                       'title': 'Demonstration time-series',
                       'notes': 'This series contains power consumption data in kW',
                       'is_public': True,
                       'is_virtual': False,
                       'config': {'timezone_name': 'Europe/London',
                                  'start': '1970-01-01T00:00:00+00:00',
                                  'series_type': 'period',
                                  'interval': 1800,
                                  'archives': [{'aggregation_type': 'average',
                                                'aggregation': 1,
                                                'count': 10000},
                                               {'aggregation_type': 'average',
                                                'aggregation': 48,
                                                'count': 10000},
                                               {'aggregation_type': 'min',
                                                'aggregation': 48,
                                                'count': 10000},
                                               {'aggregation_type': 'max',
                                                'aggregation': 48,
                                                'count': 10000}]}}

    def load_demo_data(self):
        try:
            timeseries = TimeSeries.objects.get(slug=self.demo_timeseries['slug'])
            timeseries.delete()
        except TimeSeries.DoesNotExist:
            pass

        timeseries = TimeSeries(**self.demo_timeseries)
        timeseries.save()

        data_path = os.path.join(os.path.dirname(openorg_timeseries.demo.__file__),
                                 'data', 'example_data.csv')

        readings = []
        with open(data_path, 'r') as f:
            reader = csv.reader(f)
            for row in reader:
                readings.append([dateutil.parser.parse(row[0]),
                                 float(row[1])])
        timeseries.append(readings)
