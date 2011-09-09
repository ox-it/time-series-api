import math
import os
import re
import time

import dateutil.parser
import rdflib
from rdflib.namespace import RDF

from django.conf import settings
from django.core.urlresolvers import reverse
from django.http import HttpResponse

from django_conneg.views import ContentNegotiatedView, HTMLView, TextView, JSONPView
from django_conneg.decorators import renderer

from openorg_timeseries.longliving.rrdtool import RRDClient, SeriesNotFound

TS = rdflib.Namespace('http://purl.org/net/time-series/')

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

class IndexView(HTMLView):
    def get(self, request):
        return self.render(request, {}, 'timeseries/index')

class ErrorView(HTMLView, JSONPView, TextView):
    _force_fallback_format = 'txt'

    def get(self, request, status_code, message):
        context = {
            'status_code': status_code,
            'error_message': message,
        }
        return self.render(request, context, 'timeseries/error')

class FetchView(JSONPView, TextView):
    def get(self, request):
        try:
            series_names = request.GET['series'].split(',')
        except KeyError:
            return EndpointView._error_view(request, 400, "You must supply a series parameter.")

        fetch_arguments = {}
        for argument, parameter in (('start', 'startTime'), ('end', 'endTime')):
            if parameter in request.GET:
                timestamp = None
                try:
                    timestamp = int(time.mktime(dateutil.parser.parse(request.GET[parameter]).timetuple()))
                except (OverflowError, ValueError):
                    try:
                        timestamp = int(request.GET[parameter])
                    except (OverflowError, ValueError):
                        return EndpointView._error_view(request, 400, "%s should be a W3C-style ISO8601 datetime, or a Unix timestamp." % parameter)
                fetch_arguments[argument] = timestamp
        if 'resolution' in request.GET:
            try:
                fetch_arguments['resolution'] = int(request.GET['resolution'])
            except ValueError:
                return EndpointView._error_view(request, 400, "resolution should be an integer number of seconds.")

        client = RRDClient()
        context = {
            'series': {}
        }

        for series in series_names:
            filename = os.path.join(settings.TIMESERIES_PATH, series + '.rrd')
            if not os.path.exists(filename):
                context['series'][series] = {'error': 'not-found'}
                continue

            result = client.fetch(series, **fetch_arguments)
            context['series'][series] = {
                'name': series,
                'data': [{'ts': ts, 'val': val} for ts, val in result],
            }

        return self.render(request, context, 'timeseries/fetch')

    def spool_csv(self, context):
        NaN = float('nan')
        def quote(value):
            if value is None:
                return ''
            value = value.replace('"', '""')
            if any(bad_char in value for bad_char in '\n" ,'):
                value = '"%s"' % value
            return value
        for series in context['series']:
            name, data = series, context['series'][series]['data']
            for datum in data:
                val = '' if math.isnan(datum['val']) else str(datum['val'])
                yield ",".join(quote(value) for value in (name, datum['ts'].strftime('%Y-%m-%dT%H:%M:%SZ'), val))
                yield '\n'

    @renderer(format='csv', mimetypes=('text/csv',), name="CSV")
    def render_csv(self, request, context, template_name):
        return HttpResponse(self.spool_csv(context), mimetype="text/csv")

class InfoView(HTMLView, JSONPView, RDFView):
    def get(self, request):
        try:
            series_names = request.GET['series'].split(',')
        except KeyError:
            return EndpointView._error_view(request, 400, "You must supply a series parameter.")

        client = RRDClient()
        context = {'series': {}}
        for series_name in series_names:
            context['series'][series_name] = {
                'name': series_name,
                'info': client.info(series_name),
            }

        return self.render(request, context, 'timeseries/info')

    def get_graph(self, request, context):
        graph = rdflib.ConjunctiveGraph()
        endpoint = rdflib.URIRef(request.build_absolute_uri(reverse('timeseries:index')))
        graph += ((endpoint, RDF.type, TS.TimeSeriesEndpoint),)
        for series in context['series'].itervalues():
            info = series['info']
            series_type = {'gauge': 'rate', 'counter': 'rate', 'absolute': 'cumulative'}[info['type']]
            timeseries = rdflib.URIRef(settings.TIME_SERIES_URI_BASE + series['name'])
            graph += ((timeseries, RDF.type, TS.TimeSeries),
                      (timeseries, TS.endpoint, endpoint),
                      (timeseries, TS.seriesName, rdflib.Literal(series['name'])),
                      (timeseries, TS.resolution, rdflib.Literal(int(info['interval']))),
                      (timeseries, TS.type, TS[series_type]))
            for i, sample in enumerate(series['info']['samples']):
                sample_uri = rdflib.URIRef('%s/%s' % (timeseries, i))
                graph += ((sample_uri, RDF.type, TS.Sampling),
                          (timeseries, TS.sampling, sample_uri),
                          (sample_uri, TS.resolution, rdflib.Literal(int(sample['resolution']))),
                          (sample_uri, TS['count'], rdflib.Literal(sample['count'])))
        return graph

class GraphView(HTMLView, JSONPView):
    def get(self, request):
        #return self.render(request, {}, 'timeseries/graph')
        return EndpointView._error_view(request, 501, 'Not yet implemented')

class EndpointView(ContentNegotiatedView):
    _SERIES_RE = re.compile(r'^[a-zA-Z_\d-]{1,32}$')

    # IndexView.as_view and ErrorView.as_view return functions, so we declare
    # it static to make sure Python doesn't try to turn it into an unbound
    # method at class creation time.
    _index_view = staticmethod(IndexView.as_view())
    _error_view = staticmethod(ErrorView.as_view())

    _views_by_command = {'fetch': FetchView.as_view(),
                         'info': InfoView.as_view(),
                         'graph': GraphView.as_view()}

    def get(self, request):
        command = request.GET.get('command', '')
        if not self._SERIES_RE.match(command):
            return self._index_view(request)

        view = self._views_by_command.get(command)
        if not view:
            return self._error_view(request, 400, "There is no such command.")

        try:
            return view(request)
        except SeriesNotFound:
            raise self._error_view(request, 404, "There is no such series.")
