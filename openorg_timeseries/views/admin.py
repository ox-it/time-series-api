import urllib
import urlparse

try:
    import json
except ImportError:
    import simplejson as json

from django.conf import settings
from django.core.urlresolvers import reverse
from django.core.exceptions import PermissionDenied
from django.http import HttpResponsePermanentRedirect, Http404, HttpResponseBadRequest
from django.views.generic import View
from django.db.models.query import QuerySet
from django.shortcuts import get_object_or_404
from django_conneg.views import JSONView, HTMLView, JSONPView, TextView
from django_conneg.http import HttpResponseSeeOther
from django_conneg.support import login_required


import openorg_timeseries
from openorg_timeseries import forms
from openorg_timeseries.models import TimeSeries

class ErrorView(HTMLView, JSONPView, TextView):
    _force_fallback_format = 'json'

    def dispatch(self, request, context, template_name):
        template_name = (template_name, 'timeseries-admin/error')
        return self.render(request, context, template_name)


class TimeSeriesView(JSONView):
    _error_view = staticmethod(ErrorView.as_view())
    _default_format = 'json'

    _json_indent = 2
    def simplify(self, value):
        if isinstance(value, QuerySet):
            return map(self.simplify, value)
        if isinstance(value, TimeSeries):
            data = {
                '_url': value.get_absolute_url(),
                'slug': value.slug,
                'title': value.title,
                'notes': value.notes,
                'is_virtual': value.is_virtual,
            }
            if value.is_virtual:
                data['equation'] = value.equation
            else:
                data['config'] = value.config
            return data
        else:
            return super(TimeSeriesView, self).simplify(value)

    def filtered_dict(self, d, keys):
        return dict((k, d.get(k)) for k in keys)

class ListView(TimeSeriesView, HTMLView, JSONPView):
    @login_required
    def dispatch(self, request):
        return super(ListView, self).dispatch(request)

    def get(self, request):
        series = TimeSeries.objects.all().order_by('slug')
        series = [s for s in series if request.user.has_perm('view_timeseries', s)]
        context = {
            'series': series,
        }
        return self.render(request, context, 'timeseries-admin/index')

    def post(self, request):
        if not request.user.has_perm('add_timeseries'):
            return self._error_view(request,
                                    {'status_code': 403,
                                     'error': 'lacking-privilege',
                                     'message': 'The authenticated user lacks the necessary privilege to create a new time-series'},
                                    'timeseries-admin/index-lacking-privilege')
        if request.META.get('CONTENT_TYPE') != 'application/json':
            return self._error_view(request,
                                    {'status_code': 400,
                                     'error': 'wrong-content-type',
                                     'message': 'Content-Type must be "application/json"'},
                                    'timeseries-admin/index-wrong-content-type')
        try:
            data = json.load(request)
        except ValueError:
            return self._error_view(request,
                                    {'status_code': 400,
                                     'error': 'invalid-json',
                                     'message': 'Could not parse request body as JSON'},
                                    'timeseries-admin/index-parse-failure')


        time_series = TimeSeries()
        fields = TimeSeries.common_fields + (('equation' if data.get('is_virtual') else 'config'),)
        for key in fields:
            try:
                setattr(time_series, key, data[key])
            except KeyError:
                return self._error_view(request,
                                        {'status_code': 400,
                                         'error': 'missing-field',
                                         'field': key,
                                         'message': 'Field "%s" was missing.' % key},
                                        'timeseries-admin/index-missing-field')
        time_series.save()
        return self.render(request,
                           {'status_code': 201,
                            'additional_headers': {'Location': request.build_absolute_uri(time_series.get_absolute_url())}},
                           'timeseries-admin/index-created')

class CreateView(HTMLView):
    def common(self, request):
        return {
            'form': forms.NewTimeSeriesForm(request.POST or None),
            'archive_formset': forms.ArchiveFormSet(request.POST or None),
        }

    @login_required
    def dispatch(self, request):
        if not request.user.has_perm('add_timeseries'):
            return self._error_view(request,
                                    {'status_code': 403,
                                     'error': 'lacking-privilege',
                                     'message': 'The authenticated user lacks the necessary privilege to create a new time-series'},
                                    'timeseries-admin/index-lacking-privilege')
        super(CreateView, self).dispatch(request, self.common(request))

    def get(self, request, context):
        return self.render(request, context, 'timeseries-admin/create')

    def post(self, request, context):
        form, archive_formset = context['form'], context['archive_formset']
        if not (form.is_valid() and archive_formset.is_valid()):
            return self.render(request, context, 'timeseries-admin/create')

        time_series = TimeSeries(**self.filtered_dict(form.cleaned_data,
                                                      TimeSeries.field_groups['common']))
        if time_series.is_virtual:
            for k in TimeSeries.field_groups['virtual']:
                setattr(time_series, k, form.cleaned_data.get(k))
        else:
            config = self.filtered_dict(form.cleanded_data, TimeSeries.field_groups['config'])
            config['archives'] = [f.cleaned_data for f in archive_formset.forms if f.is_valid() and f.cleaned_data]
            time_series.config = config
        time_series.save()
        return HttpResponseSeeOther(time_series.get_absolute_url())


class DetailView(HTMLView, JSONPView, TimeSeriesView):
    def common(self, request, slug):
        context = {}
        context['series'] = get_object_or_404(TimeSeries, slug=slug)
        return context

    def get(self, request, slug):
        context = self.common(request, slug)
        if not context['series'].is_public:
            raise Http404
        return self.render(request, context, 'timeseries/rest-detail')

class SecureView(View):
    force_https = getattr(settings, 'FORCE_ADMIN_HTTPS', True)

    def dispatch(self, request, *args, **kwargs):
        if self.force_https and not (settings.DEBUG or request.is_secure()):
            url = urlparse.urlparse(request.build_absolute_uri())
            url = urlparse.urlunparse(('https',) + url[1:])
            return HttpResponsePermanentRedirect(url)
        return super(SecureView, self).dispatch(request, *args, **kwargs)

class AuthenticatedView(SecureView):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated():
            url = '%s?%s' % (settings.LOGIN_URL,
                             urllib.urlencode({'next': request.build_absolute_uri()}))
            return HttpResponseSeeOther(url)
        return super(AuthenticatedView, self).dispatch(request, *args, **kwargs)

class AdminListView(HTMLView, JSONPView, AuthenticatedView):
    pass

class AdminDetailView(AuthenticatedView, DetailView):
    def common(self, request, slug):
        context = super(AdminDetailView, self).common(request, slug)
        if not request.user.has_perm('view_timeseries', context['series']):
            raise PermissionDenied
        form_class = VirtualTimeSeriesForm if context['series'].is_virtual else RealTimeSeriesForm
        context['form'] = form_class(request.POST or None, instance=context['series'])
        return context

    def get(self, request, slug):
        context = self.common(request, slug)
        return self.render(request, context, 'timeseries/admin-detail')

    def post(self, request, slug):
        context = self.common(request, slug)
        return self.render(request, context, 'timeseries/admin-detail')

