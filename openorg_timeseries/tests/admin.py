import base64
import unittest

try:
    import json
except ImportError:
    import simplejson as json

import dateutil.parser
from django.conf import settings
from django.test import TestCase

from openorg_timeseries.longliving.database import get_client

class RESTCreationTestCase(TestCase):
    fixtures = ['test_users.json']

    real_timeseries = {'slug': 'test',
                       'title': 'Title',
                       'notes': 'Notes',
                       'is_public': True,
                       'is_virtual': False,
                       'config': {'start': '1970-01-01T00:00:00Z',
                                  'timezone_name': 'Europe/London',
                                  'series_type': 'gauge',
                                  'interval': 1800,
                                  'archives': [{'aggregation_type': 'average',
                                                'aggregation': 1,
                                                'count': 10000}]}}

    def testUnauthorized(self):
        response = self.client.post('/admin/')
        self.assertEqual(response.status_code, 401)
        self.assertTrue('WWW-Authenticate' in response)
        self.assertEqual(response['WWW-Authenticate'], 'Basic')

    def testWrongContentType(self):
        response = self.client.post('/admin/',
                                    data=self.real_timeseries,
                                    REMOTE_USER='superuser')
        self.assertEqual(response.status_code, 400)

    def testCreateReal(self):
        response = self.client.post('/admin/',
                                    data=json.dumps(self.real_timeseries),
                                    content_type='application/json',
                                    REMOTE_USER='superuser')

        self.assertEqual(response.status_code, 201)
        self.assertTrue('Location' in response)

        client = get_client()
        config = client.get_config(self.real_timeseries['slug'])

        original_config = dict(self.real_timeseries['config'])
        original_config['start'] = dateutil.parser.parse(original_config['start'])

        self.assertEqual(config, original_config)
