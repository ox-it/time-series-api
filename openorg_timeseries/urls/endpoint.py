from django.conf.urls.defaults import patterns, url

from openorg_timeseries.views import endpoint as endpoint_views

urlpatterns = patterns('',
    url(r'^$',
        endpoint_views.EndpointView.as_view(),
        name='index'),
    url(r'^documentation/$',
        endpoint_views.DocumentationView.as_view(),
        name='documentation'),
)
