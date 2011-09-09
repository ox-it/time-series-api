from django.conf.urls.defaults import *

from openorg_timeseries.views import EndpointView

urlpatterns = patterns('',
    (r'^$', EndpointView.as_view(), {}, 'index')
)
