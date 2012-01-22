import copy
import csv
import httplib
import os
import urlparse

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

class TimeSeriesTestCase(TestCase):
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
    def tearDown(self):
        for path in ('csv', 'tsdb'):
            path = os.path.join(settings.TIME_SERIES_PATH, path)
            for filename in os.listdir(path):
                os.unlink(os.path.join(path, filename))
        TimeSeries.objects.all().delete()


class ListPermissionTestCase(TimeSeriesTestCase):

    def testUnauthorized(self):
        response = self.client.get('/admin/')
        self.assertEqual(response.status_code, httplib.UNAUTHORIZED)
        self.assertTrue('WWW-Authenticate' in response)
        self.assertTrue(response['WWW-Authenticate'].startswith('Basic '))

    def getTimeSeries(self, username):
        response = self.client.get('/admin/',
                                   content_type='application/json',
                                   REMOTE_USER=username)
        self.assertEqual(response.status_code, httplib.OK)
        self.assertEqual(response['Content-type'], 'application/json')
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


class RESTCreationTestCase(TimeSeriesTestCase):

    def testUnauthorized(self):
        response = self.client.post('/admin/')
        self.assertEqual(response.status_code, httplib.UNAUTHORIZED)
        self.assertTrue('WWW-Authenticate' in response)
        self.assertTrue(response['WWW-Authenticate'].startswith('Basic '))

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
                         set('openorg_timeseries.%s_timeseries' % p for p in 'view change append delete'.split()))

    def testCreateAlreadyExisting(self):
        data = copy.deepcopy(self.real_timeseries)
        data['slug'] = 'already-existing'

        TimeSeries.objects.get(slug='already-existing')

        response = self.client.post('/admin/',
                                    data=json.dumps(data),
                                    content_type='application/json',
                                    REMOTE_USER='withaddperm')

        # Check that we get a conflict response
        self.assertEqual(response.status_code, httplib.CONFLICT)

class DetailTestCase(TimeSeriesTestCase):
    readings = {
        'json': json.dumps({'readings': [{'ts': '1970-01-01T00:30:00+00:00', 'val': 5},
                                         {'ts': 3600000, 'val': 10},
                                         {'ts': '1970-01-01 01:30:00Z', 'val': 15},
                                         ['1970-01-01 03:00+01:00', 20]]}),
        'csv': '\n'.join(["1970-01-01T00:30:00+00:00,5",
                    "1970-01-01 01:00:00Z,10",
                    "1970-01-01 01:30:00Z,15",
                    "1970-01-01 03:00+01:00,20"]),
        'expected': [['1970-01-01T01:30:00+01:00', '5.0'],
                     ['1970-01-01T02:00:00+01:00', '10.0'],
                     ['1970-01-01T02:30:00+01:00', '15.0'],
                     ['1970-01-01T03:00:00+01:00', '20.0']]}

    def setUp(self):
        response = self.client.post('/admin/',
                                    data=json.dumps(self.real_timeseries),
                                    content_type='application/json',
                                    REMOTE_USER='withaddperm')
        self.location = response['Location']

class RESTDetailTestCase(DetailTestCase):
    def testGet(self):
        response = self.client.get(self.location,
                                   content_type='application/json',
                                   REMOTE_USER='withaddperm')

        self.assertEqual(response.status_code, httplib.OK)
        self.assertEqual(response['Content-type'], 'application/json')
        body = json.loads(response._get_content())

    def testPostJSON(self):
        self.postReadings('application/json', 'json')

    def testPostCSV(self):
        self.postReadings('text/csv', 'csv')

    def postReadings(self, content_type, key):
        response = self.client.post(self.location,
                                    data=self.readings[key],
                                    content_type=content_type,
                                    REMOTE_USER='withaddperm',
                                    HTTP_ACCEPT='application/json')

        self.assertEqual(response.status_code, httplib.OK, response._get_content())

        body = json.loads(response._get_content())
        self.assertEqual(body['readings']['count'], len(self.readings['expected']))
        self.assertEqual(body['readings']['appended'], len(self.readings['expected']))

        with open(os.path.join(settings.TIME_SERIES_PATH, 'csv', self.real_timeseries['slug'] + '.csv')) as f:
            reader = csv.reader(f)
            self.assertSequenceEqual(list(reader), self.readings['expected'])

        response = self.client.post(self.location,
                                    data=self.readings[key],
                                    content_type=content_type,
                                    REMOTE_USER='withappendperm',
                                    HTTP_ACCEPT='application/json')

        self.assertEqual(response.status_code, httplib.OK, response._get_content())

        body = json.loads(response._get_content())
        self.assertEqual(body['readings']['count'], len(self.readings['expected']))
        self.assertEqual(body['readings']['appended'], 0)

    def testPostInvalidJSON(self):
        response = self.client.post(self.location,
                                    data='bad json',
                                    content_type='application/json',
                                    REMOTE_USER='withaddperm')
        self.assertEqual(response.status_code, httplib.BAD_REQUEST)


    def testDelete(self):
        self.assertEqual(TimeSeries.objects.filter(slug=self.real_timeseries['slug']).count(), 1)

        response = self.client.delete(self.location,
                                      content_type='application/json',
                                      REMOTE_USER='withaddperm')

        self.assertEqual(response.status_code, httplib.NO_CONTENT)
        self.assertEqual(response._get_content(), '')

        self.assertRaises(TimeSeries.DoesNotExist,
                          TimeSeries.objects.get,
                          slug=self.real_timeseries['slug'])

    def testJSONChange(self):
        request_body = {'title': 'new title',
                        'notes': 'new notes'}
        response = self.client.post(self.location,
                                    data=json.dumps(request_body),
                                    content_type='application/json',
                                    REMOTE_USER='withaddperm')

        self.assertEqual(response.status_code, httplib.OK, response._get_content())
        body = json.loads(response._get_content())

        # Check that the change was successfully reported
        self.assertEqual(set(body['updated']), set(request_body))

        # Check that the series has actually been updated
        series = TimeSeries.objects.get(slug=self.real_timeseries['slug'])
        self.assertEqual(series.title, request_body['title'])
        self.assertEqual(series.notes, request_body['notes'])

    def testJSONChangeUnprivileged(self):
        request_body = {'title': 'new title',
                        'notes': 'new notes'}
        response = self.client.post(self.location,
                                    data=json.dumps(request_body),
                                    content_type='application/json',
                                    REMOTE_USER='unprivileged')

        self.assertEqual(response.status_code, httplib.FORBIDDEN)

class FormDetailTestCase(DetailTestCase):
    def testFormChange(self):
        form_data = {'title': 'new title',
                     'notes': 'new notes'}
        response = self.client.post(self.location,
                                    data=form_data,
                                    REMOTE_USER='withaddperm',
                                    HTTP_ACCEPT='text/html')
        self.assertEqual(response.status_code, httplib.SEE_OTHER)

        # Check that the series has actually been updated
        series = TimeSeries.objects.get(slug=self.real_timeseries['slug'])
        self.assertEqual(series.title, form_data['title'])
        self.assertEqual(series.notes, form_data['notes'])

    def testEmptyRequest(self):
        response = self.client.post(self.location,
                                    data=json.dumps({}),
                                    content_type='application/json',
                                    REMOTE_USER='unprivileged')
        self.assertEqual(response.status_code, httplib.OK)

    def testCSVUpload(self):
        filename = os.path.join(os.path.dirname(__file__), 'data', 'test_data.csv')
        with open(filename) as csv_file:
            response = self.client.post(self.location,
                                        data={'readings': csv_file},
                                        REMOTE_USER='withaddperm',
                                        HTTP_ACCEPT='text/html',
                                        follow=False)

        self.assertEqual(response.status_code, httplib.SEE_OTHER)

        # Check that the time-series was updated
        with open(os.path.join(settings.TIME_SERIES_PATH, 'csv', self.real_timeseries['slug'] + '.csv')) as f:
            reader = csv.reader(f)
            self.assertSequenceEqual(list(reader), self.readings['expected'])

        # Check that the result of the upload made it into the query string
        location = urlparse.urlparse(response['Location'])
        query = urlparse.parse_qs(location.query)
        self.assertEqual(query.get('readings.count'), ['4'])
        self.assertEqual(query.get('readings.appended'), ['4'])

class CreateViewTestCase(TimeSeriesTestCase):
    def testGet(self):
        response = self.client.get('/admin/create/',
                                   REMOTE_USER='withaddperm',
                                   HTTP_ACCEPT='text/html')
        self.assertEqual(response.status_code, httplib.OK, response._get_content())
        self.assertEqual(response['Content-type'], 'text/html', response._get_content())

    def testPost(self):
        data = {'slug': 'test',
                'title': 'Title',
                'notes': 'Notes',
                'is_public': 'on',
                'is_virtual': '',
                'start': '1970-01-01T00:00:00',
                'timezone_name': 'Europe/London',
                'series_type': 'gauge',
                'interval': '1800',
                'form-TOTAL_FORMS': '3',
                'form-INITIAL_FORMS': '0',
                'form-0-aggregation_type': 'average',
                'form-0-aggregation': '1',
                'form-0-count': '10000'}
        response = self.client.post('/admin/create/',
                                    data=data,
                                    REMOTE_USER='withaddperm',
                                    HTTP_ACCEPT='text/html',
                                    follow=False)
        self.assertEqual(response.status_code, httplib.SEE_OTHER, response._get_content())



