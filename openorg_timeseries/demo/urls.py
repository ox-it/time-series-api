from django.conf import settings
from django.conf.urls.defaults import patterns, url, include
from django.conf.urls.static import static
from django.contrib import admin

admin.autodiscover()

urlpatterns = patterns('',
    url('^', include('openorg_timeseries.urls.endpoint', 'timeseries-endpoint')),
    url('^time-series-admin/', include('openorg_timeseries.urls.admin', 'timeseries-admin')),
    url('^admin/', include(admin.site.urls)),
    url('^login/$', 'django.contrib.auth.views.login', name='auth-login'),
    url('^logout/$', 'django.contrib.auth.views.logout', name='auth-logout'),

) + static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
