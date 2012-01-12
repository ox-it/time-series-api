from __future__ import division

from django.template import Library

register = Library()

@register.filter
def prettify_seconds(value):
    if value in prettify_seconds.common:
        return prettify_seconds.common[value]
    for period, name in prettify_seconds.durations:
        if value > period:
            return '%f %s' % (value / period, name)
    return 'very often'

prettify_seconds.common = {1800: 'half-hourly',
                           3600: 'hourly',
                           86400: 'daily',
                           604800: 'weekly'}
prettify_seconds.durations = ((604800, 'weeks'),
                              (86400, 'days'),
                              (3600, 'hours'),
                              (60, 'minutes'),
                              (1, 'seconds'))
