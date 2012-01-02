DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'TEST_NAME': 'test_db.sqlite',
    },
}

INSTALLED_APPS = [
    'openorg_timeseries',
]
