import calendar
import csv
import datetime
import httplib
import urllib
import urlparse

try:
    import json
except ImportError:
    import simplejson as json

import dateutil
from django.db import IntegrityError
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.forms.util import ErrorList
from django.http import HttpResponsePermanentRedirect, HttpResponse
from django.utils.decorators import method_decorator
from django.views.generic import View
from django.db.models.query import QuerySet
from django.shortcuts import get_object_or_404
from django_conneg.views import JSONView, HTMLView, JSONPView, TextView
from django_conneg.http import HttpResponseSeeOther
#from django_conneg.support import login_required
import pytz

import openorg_timeseries
from openorg_timeseries import forms
from openorg_timeseries.models import TimeSeries

class ErrorView(HTMLView, JSONPView, TextView):
    _force_fallback_format = 'json'

    def dispatch(self, request, context, template_name):
        template_name = (template_name, 'timeseries-admin/error')
        return self.render(request, context, template_name)

class TimeSeriesView(JSONView):
    _error = staticmethod(ErrorView.as_view())
    _default_format = 'json'

    def has_perm(self, perm, obj=None):
        perm = 'openorg_timeseries.%s_timeseries' % perm
        has_perm = self.request.user.has_perm
        if obj and has_perm(perm, obj):
            return True
        return has_perm(perm)


    def error(self, status_code, **kwargs):
        return self._error(self.request,
                           dict(status_code=status_code, **kwargs),
                           'timeseries-admin/error')

    def lacking_privilege(self, action):
        return self._error(self.request,
                           {'status_code': httplib.FORBIDDEN,
                            'error': 'lacking-privilege',
                            'message': 'The authenticated user lacks the necessary privilege to %s' % action},
                           'timeseries-admin/error-lacking-privilege')

    def invalid_json(self, exception):
        return self._error(self.request,
                           {'status_code': httplib.BAD_REQUEST,
                            'error': 'invalid-json',
                            'message': 'Could not parse request body as JSON',
                            'detail': unicode(exception)},
                           'timeseries-admin/error-invalid-json')

    def bad_request(self, error, message):
        return self._error(self.request,
                           {'status_code': httplib.BAD_REQUEST,
                            'error': error,
                            'message': message},
                           'timeseries-admin/error-bad-request')

    _json_indent = 2
    def simplify(self, value):
        if isinstance(value, datetime.datetime):
            return 1000 * calendar.timegm(value.astimezone(pytz.utc).timetuple())
        if isinstance(value, QuerySet):
            return map(self.simplify, value)
        if isinstance(value, TimeSeries):
            data = {
                '_url': value.get_admin_url(),
                'slug': value.slug,
                'title': value.title,
                'notes': value.notes,
                'is_virtual': value.is_virtual,
            }
            if value.is_virtual:
                data['equation'] = value.equation
            else:
                data['config'] = value.config
            return self.simplify(data)
        else:
            return super(TimeSeriesView, self).simplify(value)

    def filtered_dict(self, d, keys):
        return dict((k, d.get(k)) for k in keys)

class ListView(TimeSeriesView, HTMLView, JSONPView):
    @method_decorator(login_required)
    def get(self, request):
        series = TimeSeries.objects.all().order_by('slug')
        series = [s for s in series if self.has_perm('view', s)]
        context = {
            'series': series,
            'server': {'name': 'openorg_timeseries.admin',
                       'version': openorg_timeseries.__version__},
        }
        return self.render(request, context, 'timeseries-admin/index')

    @method_decorator(login_required)
    def post(self, request):
        if not self.has_perm('add'):
            return self.lacking_privilege('create a new time-series')
        if request.META.get('CONTENT_TYPE') != 'application/json':
            return self.bad_request('wrong-content-type', 'Content-Type must be "application/json"')
        try:
            data = json.load(request)
        except ValueError, e:
            return self._error.invalid_json(request, e)


        time_series = TimeSeries()
        fields = TimeSeries.common_fields + (('equation' if data.get('is_virtual') else 'config'),)
        for key in fields:
            try:
                setattr(time_series, key, data[key])
            except KeyError:
                return self.error(httplib.BAD_REQUEST,
                                  error='missing-field',
                                  field=key,
                                  message='Field "%s" was missing.' % key)
        try:
            time_series.save()
        except IntegrityError:
            # A time-series already exists with the desired slug
            return self.error(httplib.CONFLICT,
                              error='already-exists',
                              message='A time-series already exists with the slug %r.' % time_series.slug)
        for perm in ('view', 'append', 'change', 'delete'):
            request.user.grant('openorg_timeseries.%s_timeseries' % perm, time_series)
        return self.render(request,
                           {'status_code': 201,
                            'additional_headers': {'Location': request.build_absolute_uri(time_series.get_admin_url())}},
                           'timeseries-admin/index-created')

class CreateView(TimeSeriesView, HTMLView):
    def common(self, request):
        return {
            'form': forms.NewTimeSeriesForm(request.POST or None),
            'archive_formset': forms.ArchiveFormSet(request.POST or None),
        }

    @method_decorator(login_required)
    def get(self, request):
        context = self.common(request)
        if not request.user.has_perm('openorg_timeseries.add_timeseries'):
            return self.lacking_privilege('create a new time-series')
        return self.render(request, context, 'timeseries-admin/create')

    @method_decorator(login_required)
    def post(self, request):
        context = self.common(request)
        if not request.user.has_perm('openorg_timeseries.add_timeseries'):
            return self.lacking_privilege('create a new time-series')
        form, archive_formset = context['form'], context['archive_formset']
        if not (form.is_valid() and archive_formset.is_valid()):
            return self.render(request, context, 'timeseries-admin/create')

        time_series = TimeSeries(**self.filtered_dict(form.cleaned_data,
                                                      TimeSeries.common_fields))
        if time_series.is_virtual:
            time_series.equation = form.cleaned_data['equation']
        else:
            config = self.filtered_dict(form.cleaned_data, TimeSeries.config_fields)
            config['archives'] = [f.cleaned_data for f in archive_formset.forms if f.is_valid() and f.cleaned_data]
            time_series.config = config

        try:
            time_series.save()
        except IntegrityError:
            form.errors['slug'] = ErrorList(["A time-series with this slug already exists."])
            return self.get(request, context)
        return HttpResponseSeeOther(time_series.get_admin_url())

class SecureView(View):
    force_https = getattr(settings, 'FORCE_ADMIN_HTTPS', True)

    def dispatch(self, request, *args, **kwargs):
        if self.force_https and not (settings.DEBUG or request.is_secure()):
            url = urlparse.urlparse(request.build_absolute_uri())
            url = urlparse.urlunparse(('https',) + url[1:])
            return HttpResponsePermanentRedirect(url)
        return super(SecureView, self).dispatch(request, *args, **kwargs)

class DetailView(TimeSeriesView, HTMLView):
    def common(self, request, slug):
        series = get_object_or_404(TimeSeries, slug=slug)
        form = forms.TimeSeriesForm(request.POST or None, instance=series)
        context = {'series': series,
                   'form': form}
        for field in ('count', 'appended'):
            if 'readings.%s' % field in request.GET:
                context['readings'] = context.get('readings', {})
                context['readings'][field] = request.GET['readings.%s' % field]
        return context

    @method_decorator(login_required)
    def get(self, request, slug, context=None):
        context = context or self.common(request, slug)
        if not self.has_perm('view', context['series']):
            return self.lacking_privilege('view this time-series')
        return self.render(request, context, 'timeseries-admin/detail')

    @method_decorator(login_required)
    def post(self, request, slug):
        context = self.common(request, slug)
        series, form = context['series'], context['form']
        successful = True

        if request.META.get('CONTENT_TYPE') == 'application/json':
            try:
                request.json_data = json.load(request)
            except ValueError, e:
                return self.invalid_json(e)
        else:
            request.json_data = None

        #context = {}
        try:
            readings = self.get_readings(request)
        except ValueError, e:
            readings, successful = None, False
            context['readings'] = {'error': e.args[0]}
        if readings and series.is_virtual:
            return self.bad_request("append-to-virtual", "You cannot append readings to a virtual time-series")
        if readings is not None and not self.has_perm('append', series):
            return self.lacking_privilege("append to this time-series")
        elif readings:
            result = series.append(readings)
            context['readings'] = {'count': len(readings),
                                   'appended': result['appended'],
                                   'last': result['last']}

        editable_fields = ('title', 'notes', 'is_public')
        if request.json_data and any(f in request.json_data for f in editable_fields):
            context['updated'] = []
            if not self.has_perm('change', series):
                return self.lacking_privilege("modify this time-series")
            for f in editable_fields:
                if f in request.json_data:
                    if not isinstance(request.json_data[f], unicode):
                        return self.bad_request("type-error", "Field '%s' must be a string" % f)
                    setattr(series, f, request.json_data[f])
                    context['updated'].append(f)
            series.save()

        if form.is_valid():
            if not self.has_perm('change', series):
                return self.lacking_privilege("modify this time-series")
            context['update-successful'] = True
            form.save()

        if successful and self.get_renderers(request)[0].format == 'html':
            query = {}
            if 'readings' in context:
                query.update({'readings.count': context['readings']['count'],
                              'readings.appended': context['readings']['appended']})
            if context.get('update-successful'):
                query['updated'] = 'true'
            return HttpResponseSeeOther('%s?%s' % (series.get_admin_url(), urllib.urlencode(query)))

        context['status_code'] = httplib.OK if successful else httplib.BAD_REQUEST

        return self.render(request, context, 'timeseries-admin/detail')

    def get_readings(self, request):
        readings = []

        if request.json_data and 'readings' in request.json_data:
            if not isinstance(request.json_data['readings'], list):
                raise ValueError('"readings" member should be a list.')
            for i, reading in enumerate(request.json_data['readings']):
                try:
                    if isinstance(reading, dict):
                        reading = reading['ts'], reading['val']
                    elif isinstance(reading, list) and len(reading) == 2:
                        pass
                    else:
                        raise ValueError("Reading %i must be either an object with 'ts' and 'val' members, or a two-element list." % i)

                    readings.append(reading)

                except KeyError, e:
                    raise ValueError("Reading %i was missing a '%s' member" % (i, e.args[0]))
                except ValueError, e:
                    raise ValueError("Reading %i: %s" % (i, e.args[0]))
        elif request.META.get('CONTENT_TYPE') == 'text/csv':
            self.parse_csv(request, readings)
        elif 'readings' in request.FILES:
            self.parse_csv(request.FILES['readings'], readings)
        else:
            return None

        parsed_readings = []
        for reading in readings:
            if isinstance(reading[0], basestring):
                ts = dateutil.parser.parse(reading[0])
            elif isinstance(reading[0], (int, float)):
                ts = pytz.utc.localize(datetime.datetime.utcfromtimestamp(reading[0] / 1000))
            if not ts.tzinfo:
                raise ValueError("Timestamp in reading %i is missing a timezone part.")
            parsed_readings.append((ts, reading[1]))

        return parsed_readings

    def parse_csv(self, fileobj, readings):
        try:
            reader = csv.reader(fileobj)
            for i, row in enumerate(reader):
                if len(row) != 2:
                    raise ValueError("Row %i doesn't have two columns" % i)
                row[1] = float(row[1])
                readings.append(row)
        except Exception, e:
            if isinstance(e, ValueError):
                raise
            raise ValueError("Couldn't parse CSV from request.")

    def append_readings(self, request, slug, readings):
        pass

    def delete(self, request, slug):
        context = self.common(request, slug)
        if not request.user.has_perm('openorg_timeseries.delete_timeseries', context['series']):
            return self._error.lacking_privilege(request, 'delete this time-series')
        context['series'].delete()
        return HttpResponse('', status=httplib.NO_CONTENT)


