import shutil
import threading

from django.conf import settings
from django.test.simple import DjangoTestSuiteRunner

from openorg_timeseries.longliving.database import DatabaseThread

class TestSuiteRunner(DjangoTestSuiteRunner):
    def setup_test_environment(self):
        self.bail = threading.Event()
        self.database_thread = DatabaseThread(self.bail)
        self.database_thread.start()

        super(TestSuiteRunner, self).setup_test_environment()

    def teardown_test_environment(self):
        super(TestSuiteRunner, self).teardown_test_environment()

        self.bail.set()
        self.database_thread.join()
        shutil.rmtree(settings.TIME_SERIES_PATH)


