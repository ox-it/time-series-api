import calendar
import datetime
import httplib
import os
import time

import dateutil.parser
import pytz
import rdflib
from rdflib.namespace import RDF, RDFS

from django.conf import settings
from django.core.urlresolvers import reverse
from django.http import HttpResponse

from django_conneg.views import ContentNegotiatedView, HTMLView, TextView, JSONPView
from django_conneg.decorators import renderer

from openorg_timeseries.longliving.database import get_client, SeriesNotFound, TimeSeriesException
from openorg_timeseries.models import TimeSeries

TS = rdflib.Namespace('http://purl.org/NET/time-series/')

class RDFView(ContentNegotiatedView):
    def render_rdflib(self, request, context, format, mimetype):
        graph = self.get_graph(request, context)
        return HttpResponse(graph.serialize(format=format), mimetype=mimetype)

    @renderer(format='rdf', mimetypes=('application/rdf+xml',), name='RDF/XML')
    def render_rdf(self, request, context, template_name):
        return self.render_rdflib(request, context, 'pretty-xml', 'application/rdf+xml')

    @renderer(format='nt', mimetypes=('text/plain',), name='N-Triples')
    def render_nt(self, request, context, template_name):
        return self.render_rdflib(request, context, 'nt', 'text/plain')

    @renderer(format='ttl', mimetypes=('text/turtle',), name='Turtle')
    def render_ttl(self, request, context, template_name):
        return self.render_rdflib(request, context, 'ttl', 'text/plain')

    @renderer(format='n3', mimetypes=('text/n3',), name='Notation3')
    def render_n3(self, request, context, template_name):
        return self.render_rdflib(request, context, 'n3', 'text/n3')

class TabularView(ContentNegotiatedView):
    def get_table(self):
        raise NotImplementedError

    def _spool_csv(self, request, context):
        table = self.get_table(request, context)
        def quote(value):
            if value is None:
                return ''
            value = value.replace('"', '""')
            if any(bad_char in value for bad_char in '\n" ,'):
                value = '"%s"' % value
            return value

        for row in table:
            yield ",".join(map(quote, row))
            yield '\n'

    @renderer(format='csv', mimetypes=('text/csv',), name='CSV')
    def render_csv(self, request, context, template_name):
        return HttpResponse(self._spool_csv(request, context), mimetype="text/csv")

class IndexView(HTMLView):
    def get(self, request):
        return self.render(request, {}, 'timeseries/index')

class ErrorView(HTMLView, JSONPView, TextView):
    _force_fallback_format = 'txt'
    _json_indent = 2

    def get(self, request, status_code, message):
        context = {
            'status_code': status_code,
            'code': status_code,
            'response_text': httplib.responses[status_code],
            'error_message': message,
        }
        return self.render(request, context, 'timeseries/error')

class FetchView(JSONPView, TextView, TabularView):
    _json_indent = 1

    def get(self, request):
        try:
            series_names = set(request.GET['series'].split(','))
        except KeyError:
            return EndpointView._error_view(request, 400, "You must supply a series parameter.")

        fetch_arguments = {}
        try:
            fetch_arguments['aggregation_type'] = request.GET['type']
            if fetch_arguments['aggregation_type'] not in ('average', 'min', 'max'):
                raise ValueError
        except (KeyError, ValueError):
            return EndpointView._error_view(request, 400, "Missing required parameter 'type', which must be one of 'average', 'min', 'max'.")

        for argument, parameter in (('period_start', 'start'), ('period_end', 'end')):
            if parameter in request.GET:
                timestamp = None
                try:
                    timestamp = dateutil.parser.parse(request.GET[parameter])
                except (OverflowError, ValueError):
                    try:
                        timestamp = int(request.GET[parameter])
                        timestamp = datetime.datetime.utcfromtimestamp(timestamp)
                    except (OverflowError, ValueError):
                        return EndpointView._error_view(request, 400, "%s should be a W3C-style ISO8601 datetime, or a Unix timestamp, which will be assumed to be UTC in the absence of any timezone information." % parameter)
                if not timestamp.tzinfo:
                    timestamp = pytz.utc.localize(timestamp)
                fetch_arguments[argument] = timestamp
        try:
            fetch_arguments['interval'] = int(request.GET['resolution'])
        except (KeyError, ValueError):
            return EndpointView._error_view(request, 400, "resolution query parameter should be an integer number of seconds.")

        print fetch_arguments

        timeseries = TimeSeries.objects.filter(is_public=True, slug__in=series_names)
        found_series = set(s.slug for s in timeseries)
        context = {
            'series': {}
        }

        for series_name in series_names:
            if series_name not in found_series:
                context['series'][series_name] = {'error': 'not-found'}

        for series in timeseries:
            try:
                result = series.fetch(**fetch_arguments)
            except TimeSeriesException:
                raise
                context['series'][series.slug] = {'error': 'type-not-available'}
                continue
            context['series'][series.slug] = {
                'name': series.slug,
                'data': [{'ts': ts, 'val': val if val == val else None} for ts, val in result],
            }

        return self.render(request, context, 'timeseries/fetch')

    def get_table(self, request, context):
        for series in context['series']:
            name, data = series, context['series'][series].get('data', [])
            for datum in data:
                # val may be NaN, which is not equal to itself. math.isnan()
                # is only available in >=Py2.6, so use this (somewhat weird-
                # looking) test.
                val = datum['val']
                val = str(val) if val == val else ''
                yield (name, datum['ts'].strftime('%Y-%m-%d %H:%M:%S'), val)

class InfoView(HTMLView, JSONPView, RDFView):
    _json_indent = 2

    series_types = {'period': 'gauge', 'gauge': 'rate', 'counter': 'gauge', 'absolute': 'cumulative'}

    def get(self, request):
        try:
            series_names = request.GET['series']
            series = TimeSeries.objects.filter(is_public=True)
            missing = []
            if series_names != '*':
                series_names = set(series_names.split(','))
                series = series.filter(slug__in=series_names)
                found_names = set(s.slug for s in series)
                missing_names = series_names - found_names
        except KeyError:
            return EndpointView._error_view(request, 400, "You must supply a series parameter.")

        context = {'series': {}}
        for s in series:
            config = s.config
            metadata = {'name': s.slug,
                        'title': s.title,
                        'notes': s.notes,
                        'info': {'type': self.series_types[config['series_type']],
                                 'interval': config['interval'],
                                 'start': config['start'],
                                 'updated': s.last,
                                 'updated_jsts': calendar.timegm(s.last.astimezone(pytz.utc).timetuple()),
                                 'timezone': config['timezone_name'],
                                 'samples': [{'count': a['count'],
                                              'type': a['aggregation_type'],
                                              'resolution': a['aggregation'] * config['interval'],
                                              'aggregation': a['aggregation']} for a in s.config['archives']]}}
            context['series'][s.slug] = metadata
        context['series_list'] = context['series'].values()

        return self.render(request, context, 'timeseries/info')

    def get_graph(self, request, context):
        graph = rdflib.ConjunctiveGraph()
        endpoint = rdflib.URIRef(request.build_absolute_uri(reverse('timeseries-endpoint:index')))
        graph += ((endpoint, RDF.type, TS.TimeSeriesEndpoint),)
        for series in context['series'].itervalues():
            info = series['info']
            timeseries = rdflib.URIRef(settings.TIME_SERIES_URI_BASE + series['name'])
            graph += ((timeseries, RDF.type, TS.TimeSeries),
                      (timeseries, TS.endpoint, endpoint),
                      (timeseries, TS.seriesName, rdflib.Literal(series['name'])),
                      (timeseries, TS.resolution, rdflib.Literal(int(info['interval']))),
                      (timeseries, TS.type, TS[info['type']]),
                      (timeseries, RDFS.label, rdflib.Literal(series['title'])))
            if series['notes']:
                graph.add((timeseries, RDFS.comment, rdflib.Literal(series['notes'])))
            for i, sample in enumerate(series['info']['samples']):
                sample_uri = rdflib.URIRef('%s/%s' % (timeseries, i))
                graph += ((sample_uri, RDF.type, TS.Sampling),
                          (timeseries, TS.sampling, sample_uri),
                          (sample_uri, TS.resolution, rdflib.Literal(int(sample['resolution']))),
                          (sample_uri, TS['count'], rdflib.Literal(sample['count'])),
                          (sample_uri, TS['samplingType'], TS[sample['type']]))
        return graph

    def preprocess_context_for_json(self, context):
        return {'series': context['series']}

class GraphView(HTMLView, JSONPView):
    _json_indent = 2

    def get(self, request):
        #return self.render(request, {}, 'timeseries/graph')
        return EndpointView._error_view(request, 501, 'Not yet implemented')

class ListView(HTMLView, JSONPView, TabularView):
    _json_indent = 2

    def get(self, request):
        series = TimeSeries.objects.filter(is_public=True)
        context = {
            'series': series,
            'names': [s.slug for s in series],
        }
        return self.render(request, context, 'timeseries/list')

    def get_table(self, request, context):
        for name in context['names']:
            yield [name]

class EndpointView(ContentNegotiatedView):
    # IndexView.as_view and ErrorView.as_view return functions, so we declare
    # it static to make sure Python doesn't try to turn it into an unbound
    # method at class creation time.
    _index_view = staticmethod(IndexView.as_view())
    _error_view = staticmethod(ErrorView.as_view())

    _views_by_action = {'fetch': FetchView.as_view(),
                        'info': InfoView.as_view(),
                        'graph': GraphView.as_view(),
                        'list': ListView.as_view()}

    def get(self, request):
        action = request.GET.get('action')
        if action is None:
            return self._index_view(request)

        view = self._views_by_action.get(action)
        if not view:
            return self._error_view(request, 400, "There is no such action. Available actions are: %s." % ', '.join(self._views_by_action))

        try:
            return view(request)
        except SeriesNotFound:
            return self._error_view(request, 404, "There is no such series. Use ?action=list to see what series are available.")

class DocumentationView(HTMLView):
    def get(self, request):
        renderers = {}
        for action, view in EndpointView._views_by_action.iteritems():
            renderers[action] = [{'format': r.format, 'mimetypes': r.mimetypes, 'name': r.name} for r in view._renderers]
        context = {
            'endpoint_url': request.build_absolute_uri(reverse('timeseries-endpoint:index')),
            'renderers': renderers,
        }
        return self.render(request, context, 'timeseries/documentation')
