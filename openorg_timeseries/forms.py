import datetime

import dateutil.parser
from django import forms
from django.conf import settings
from django.forms.util import ValidationError
from django.forms.formsets import formset_factory
import pytz

from . import models

class NewTimeSeriesForm(forms.ModelForm):
    series_type = forms.ChoiceField(choices=models.SERIES_TYPE_CHOICES,
                                    widget=forms.Select,
                                    required=False,
                                    initial='gauge',
                                    help_text='<b>gauge</b> is for things like temperature')
    interval = forms.IntegerField(required=False)
    timezone_name = forms.ChoiceField(choices=[(x, x) for x in pytz.all_timezones],
                                      required=False,
                                      initial=settings.TIME_ZONE)
    start = forms.CharField(required=False,
                            initial='1970-01-01 00:00:00',
                            help_text='The earliest point from which readings are to be inserted. Should be in the form <code>YYYY-MM-DD HH:MM:SS</code>. Interpreted in the series time zone if no time zone specified.')
    is_virtual = forms.TypedChoiceField(coerce=bool,
                                        choices=(('', 'real'), ('on', 'virtual')),
                                        widget=forms.RadioSelect,
                                        initial='',
                                        required=False)
    equation = forms.CharField(required=False)

    def clean_start(self):
        try:
            start = dateutil.parser.parse(self.cleaned_data['start'])
        except Exception, e:
            raise ValidationError(e)
        if not start.tzinfo:
            timezone = pytz.timezone(self.cleaned_data['timezone_name'])
            start = timezone.localize(start)
        return start

    def clean(self):
        if self.cleaned_data.get('is_virtual'):
            if not self.cleaned_data.get('equation'):
                self._errors['equation'] = self.error_class(['This field is required.'])
                del self.cleaned_data['equation']
        else:
            for name in 'interval start series_type timezone_name'.split():
                if not self.cleaned_data.get(name):
                    self._errors[name] = self.error_class(['This field is required.'])
                    if name in self.cleaned_data:
                        del self.cleaned_data[name]
        return self.cleaned_data


    class Meta:
        model = models.TimeSeries
        fields = ('slug', 'title', 'is_public', 'is_virtual', 'interval', 'timezone_name', 'start', 'notes')

class ArchiveForm(forms.Form):
    aggregation_type = forms.ChoiceField(widget=forms.Select,
                                         choices=(('', '-' * 8),) + models.AGGREGATION_TYPE_CHOICES)
    aggregation = forms.IntegerField()
    count = forms.IntegerField()

ArchiveFormSet = formset_factory(ArchiveForm, extra=3)

class TimeSeriesForm(forms.ModelForm):
    class Meta:
        model = models.TimeSeries
        fields = ('title', 'notes', 'is_public')
