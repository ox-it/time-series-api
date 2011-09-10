from django.conf.urls.defaults import *

from openorg_timeseries import views

urlpatterns = patterns('',
    (r'^$', views.EndpointView.as_view(), {}, 'index'),
    (r'^documentation/$', views.DocumentationView.as_view(), {}, 'doc'),
)
