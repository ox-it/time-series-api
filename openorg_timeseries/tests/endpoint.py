import httplib

from django.test import TestCase

class DocumentationTestCase(TestCase):
    def testOK(self):
        response = self.client.get('/endpoint/documentation/')
        self.assertEqual(response.status_code, httplib.OK)

