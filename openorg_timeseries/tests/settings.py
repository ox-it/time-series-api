import tempfile

DATABASES = {
    'default': {'ENGINE': 'django.db.backends.sqlite3'}
}

INSTALLED_APPS = (
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'openorg_timeseries',
)

MIDDLEWARE_CLASSES = (
    'django.middleware.common.CommonMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.auth.middleware.RemoteUserMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
#     'django.middleware.http.ConditionalGetMiddleware',
#     'django.middleware.gzip.GZipMiddleware',
)

AUTHENTICATION_BACKENDS = (
    'django.contrib.auth.backends.ModelBackend',
    'django.contrib.auth.backends.RemoteUserBackend',
)

TIME_SERIES_SERVER_ARGS = {'address': ('localhost', 18696),
                           'authkey': 'abracadabra'}
TIME_SERIES_PATH = tempfile.mkdtemp()


ROOT_URLCONF = 'openorg_timeseries.tests.urls'

TEST_RUNNER = 'openorg_timeseries.tests.runner.TestSuiteRunner'
