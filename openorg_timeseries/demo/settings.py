import os

DEBUG = True

DATA_DIR = os.path.join(os.curdir, 'demo-data')

DATABASES = {
    'default': {'ENGINE': 'django.db.backends.sqlite3',
                'NAME': os.path.join(DATA_DIR, 'db.sqlite3')},
}

INSTALLED_APPS = (
    'openorg_timeseries.demo',
    'django.contrib.auth',
    'django.contrib.admin',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.staticfiles',
    'django.contrib.messages',
    'django_conneg',
    'openorg_timeseries',
    'object_permissions',
)

MIDDLEWARE_CLASSES = (
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django_conneg.support.middleware.BasicAuthMiddleware',
)

ROOT_URLCONF = 'openorg_timeseries.demo.urls'

STATIC_ROOT = os.path.join(DATA_DIR, 'static')
STATIC_URL = '/static/'

LOGIN_URL = '/login/'
LOGOUT_URL = '/logout/'
LOGIN_REDIRECT_URL = '/'


TIME_SERIES_URI_BASE = "http://id.example.org/time-series/"
TIME_SERIES_SERVER_ARGS = {'address': ('localhost', 28349),
                           'authkey': 'thisisasecret'}
TIME_SERIES_PATH = DATA_DIR
