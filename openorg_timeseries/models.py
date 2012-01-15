import datetime
import pickle

import dateutil.parser
from django.conf import settings
from django.contrib.auth.models import User
from django.core.urlresolvers import reverse
from django.db import IntegrityError
from django.db import models
import object_permissions
import pytz

from . import combine
from openorg_timeseries.longliving.database import get_client

SERIES_TYPE_CHOICES = (
    ('counter', 'Counter'),
    ('period', 'Period'),
    ('gauge', 'Gauge'),
)

AGGREGATION_TYPE_CHOICES = (
    ('average', 'Average'),
    ('min', 'Minimum'),
    ('max', 'Maximum'),
)

class TimeSeries(models.Model):
    slug = models.SlugField(unique=True, db_index=True)
    title = models.CharField(max_length=80)
    notes = models.TextField(blank=True)
    is_public = models.BooleanField(default=True)

    is_virtual = models.BooleanField()

    # Real time-series data
    _config = models.TextField(blank=True)
    _config_new = None
    _last = models.DateTimeField(null=True, blank=True)

    # Virtual time-series data
    equation = models.TextField()
    depends_on = models.ManyToManyField('self',
                                        related_name='dependents',
                                        symmetrical=False,
                                        blank=True)

    common_fields = ('slug', 'title', 'notes', 'is_public', 'is_virtual')
    config_fields = ('interval', 'start', 'series_type', 'timezone_name')


    def _get_config(self):
        if self._config_new:
            return self._config_new
        elif not self._config:
            return None
        else:
            self._config_new = pickle.loads(self._config.encode('ascii'))
            return self._config_new
    def _set_config(self, value):
        if self.pk:
            raise IntegrityError("Cannot change the config of a pre-existing series.")

        if not isinstance(value, dict):
            raise ValueError("meta must be a dictionary")
        if not isinstance(value.get('interval'), int) or not value['interval'] > 0:
            raise ValueError("interval member must be a positive integer")

        if isinstance(value.get('start'), int):
            value['start'] = datetime.datetime.fromtimestamp(value['start'] / 1000)
        if isinstance(value.get('start'), basestring):
            value['start'] = dateutil.parser.parse(value['start'])
        if not isinstance(value.get('start'), datetime.datetime):
            raise ValueError("start must be a datetime, a date string, or a JS timestamp")
        if not value['start'].tzinfo:
            value['start'] = pytz.utc.localize(value['start'])

        if value.get('timezone_name') not in pytz.all_timezones:
            raise ValueError("timezone_name must be in the Olsen database (given %r)" % value.get('timezone_name'))


        archives = value.get('archives')
        if not isinstance(archives, list):
            raise ValueError("archives member must be a list")
        for i, archive in enumerate(archives):
            if archive.get('aggregation_type') not in u'average min max'.split():
                raise ValueError("aggregation_type for element %d must be one of {'average', 'min', 'max'}, not %r" % (i, archive.get('aggregation_type')))
            if not isinstance(archive.get('count'), int):
                raise ValueError("count for element %d must be an integer" % i)
            if not isinstance(archive.get('aggregation'), int):
                raise ValueError("aggregation for element %d must be an integer" % i)
        self._config_new = value
    config = property(_get_config, _set_config)

    def _get_last(self):
        if not self._last:
            return None
        tz = pytz.timezone(self.config['timezone_name'])
        return tz.localize(self._last)
    def _set_last(self, value):
        self._last = value.astimezone(pytz.utc)
    last = property(_get_last, _set_last)

    class Meta:
        permissions = (
            ('append_timeseries', 'User can append new readings to this time-series'),
        )

    def save(self, *args, **kwargs):
        if self._config_new is not None:
            self._config = pickle.dumps(self._config_new)

        if self.is_virtual:
            equation = combine.evaluate_equation(self.equation)
        #else:
        #    tz = pytz.timezone(settings.TIME_ZONE)
        #    start = self.config['start']
        #    if self.config.start.tzinfo:
        #        self.start = self.start.astimezone(tz)
        #    else:
        #        self.start = tz.localize(self.start)

        create_timeseries = not self.is_virtual and not self.pk

        super(TimeSeries, self).save(*args, **kwargs)

        if create_timeseries:
            database_client = get_client()
            database_client.create(self.slug, **self.config)

        if self.is_virtual:
            self.depends_on = TimeSeries.objects.filter(slug__in=equation.get_slugs())
        else:
            for vts in self.dependents.all():
                vts.save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.dependents.all().count():
            raise IntegrityError("This series has dependent virtual series.")
        if not self.is_virtual:
            database_client = get_client()
            database_client.delete(self.slug)
        super(TimeSeries, self).delete(*args, **kwargs)

    def append(self, readings):
        database_client = get_client()
        result = database_client.append(self.slug, readings)
        self.last = result['last']
        self.save()
        return result

    def fetch(self, aggregation_type, interval, period_start=None, period_end=None):
        database_client = get_client()
        return database_client.fetch(self.slug, aggregation_type, interval, period_start, period_end)

    def get_admin_url(self):
        return reverse('timeseries-admin:detail', args=[self.slug])

    def __unicode__(self):
        return "%s (%s)" % (self.title, self.slug)

object_permissions.register(['openorg_timeseries.view_timeseries',
                             'openorg_timeseries.append_timeseries',
                             'openorg_timeseries.change_timeseries',
                             'openorg_timeseries.delete_timeseries'],
                            TimeSeries, 'openorg_timeseries')
