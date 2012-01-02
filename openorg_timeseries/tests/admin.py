import base64
import copy
import unittest

try:
    import json
except ImportError:
    import simplejson as json

import dateutil.parser
from django.conf import settings
from django.test import TestCase
from django.contrib.auth.models import User

from openorg_timeseries.models import TimeSeries
from openorg_timeseries.longliving.database import get_client

class ListPermissionTestCase(TestCase):
    fixtures = ['test_users.json', 'test_timeseries.json']

    def testUnauthorized(self):
        response = self.client.get('/admin/')
        self.assertEqual(response.status_code, 401)
        self.assertTrue('WWW-Authenticate' in response)
        self.assertEqual(response['WWW-Authenticate'], 'Basic')

    def getTimeSeries(self, username):
        response = self.client.get('/admin/',
                                   content_type='application/json',
                                   REMOTE_USER=username)
        body = json.loads(response._get_content())
        return set(s['slug'] for s in body['series'])

    def testSuperuser(self):
        series = self.getTimeSeries("superuser")
        expected_series = set(ts.slug for ts in TimeSeries.objects.all())
        self.assertEqual(series, expected_series)

    def testUnprivileged(self):
        series = self.getTimeSeries("unprivileged")
        self.assertEqual(series, set())

    def testObjectPerm(self):
        series = self.getTimeSeries("withobjectperm")
        self.assertEqual(series, set(['perm-test-one']))


class RESTCreationTestCase(TestCase):
    fixtures = ['test_users.json', 'test_timeseries.json']

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

    def testUnprivileged(self):
        response = self.client.post('/admin/',
                                    REMOTE_USER='unprivileged')
        self.assertEqual(response.status_code, 403)

    def testWrongContentType(self):
        response = self.client.post('/admin/',
                                    data=self.real_timeseries,
                                    REMOTE_USER='superuser')
        self.assertEqual(response.status_code, 400)

    def testCreateReal(self):
        response = self.client.post('/admin/',
                                    data=json.dumps(self.real_timeseries),
                                    content_type='application/json',
                                    REMOTE_USER='withaddperm')

        self.assertEqual(response.status_code, 201)
        self.assertTrue('Location' in response)

        # Check that the config matches that we supplied
        client = get_client()
        config = client.get_config(self.real_timeseries['slug'])
        original_config = dict(self.real_timeseries['config'])
        # We provided an ISO8601 timestamp, which will have been converted to a datetime
        original_config['start'] = dateutil.parser.parse(original_config['start'])
        self.assertEqual(config, original_config)

        # Check that we have permissions to do everything to this timeseries
        time_series = TimeSeries.objects.get(slug=self.real_timeseries['slug'])
        user = User.objects.get(username='withaddperm')
        self.assertEqual(set(user.get_perms(time_series)),
                         set(['view_timeseries', 'change_timeseries',
                              'append_timeseries', 'delete_timeseries']))

    def testCreateAlreadyExisting(self):
        data = copy.deepcopy(self.real_timeseries)
        data['slug'] = 'already-existing'

        TimeSeries.objects.get(slug='already-existing')

        response = self.client.post('/admin/',
                                    data=json.dumps(data),
                                    content_type='application/json',
                                    REMOTE_USER='withaddperm')

        # Check that we get a conflict response
        self.assertEqual(response.status_code, 409)
