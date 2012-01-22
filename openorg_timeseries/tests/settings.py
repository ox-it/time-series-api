import tempfile

DATABASES = {
    'default': {'ENGINE': 'django.db.backends.sqlite3'}
}

INSTALLED_APPS = (
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django_conneg',
    'openorg_timeseries',
    'openorg_timeseries.tests',
)

MIDDLEWARE_CLASSES = (
    'django.middleware.common.CommonMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.auth.middleware.RemoteUserMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django_conneg.support.middleware.BasicAuthMiddleware',
#     'django.middleware.http.ConditionalGetMiddleware',
#     'django.middleware.gzip.GZipMiddleware',
)

AUTHENTICATION_BACKENDS = (
    'django.contrib.auth.backends.ModelBackend',
    'django.contrib.auth.backends.RemoteUserBackend',
    'object_permissions.backend.ObjectPermBackend',
)

TIME_SERIES_SERVER_ARGS = {'address': ('localhost', 18696),
                           'authkey': 'abracadabra'}
TIME_SERIES_PATH = tempfile.mkdtemp()


ROOT_URLCONF = 'openorg_timeseries.tests.urls'

TEST_RUNNER = 'openorg_timeseries.tests.runner.TestSuiteRunner'

# for django_conneg, so that the basic auth middleware kicks in.
BASIC_AUTH_ALLOW_HTTP = True
