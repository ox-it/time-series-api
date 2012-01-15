from django.conf.urls.defaults import patterns, url, include

urlpatterns = patterns('',
    url(r'^endpoint/', include('openorg_timeseries.urls.endpoint', 'timeseries-endpoint')),
    url(r'^admin/', include('openorg_timeseries.urls.admin', 'timeseries-admin')),
)
