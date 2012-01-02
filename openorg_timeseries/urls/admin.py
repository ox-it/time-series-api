from django.conf.urls.defaults import patterns, url

from openorg_timeseries.views import admin as admin_views

urlpatterns = patterns('',
    url(r'^$', admin_views.ListView.as_view(), name='index'),
    url(r'^create/$', admin_views.CreateView.as_view(), name='create'),
    url(r'^(?P<slug>[a-zA-Z\d\-_]+)/$', admin_views.DetailView.as_view(), name='detail'),
)

