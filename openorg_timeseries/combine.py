# -*- coding: utf-8 -*-
from __future__ import division

"""
Classes implementing the combination of time-series.
"""

import operator
import re

class ReplaceMeWith(Exception):
    def __init__(self, timeseries):
        self.timeseries = timeseries

def combine(op, operands):
    operands = [o.get_evaluator() for o in operands]
    def f(readings):
        return op(*[operand(readings) for operand in operands])
    return f

def identity(slug):
    def f(readings):
        return readings[slug]
    return f

def combine_time_series(operands, op, symbol, tightness):
    display = (' %s ' % symbol).join([('(%s)' if tightness > t.tightness else '%s') % t.display for t in operands])
    #display = "(%s) %s (%s)" % (self.display, symbol, other.display)
    return TimeSeries(func=combine(operands, op),
                      slugs=frozenset(reduce(operator.or_, (o.slug for o in operands))),
                      display=display, tightness=tightness)



class TimeSeries(object):
    tightness = 10

    def __init__(self, slug):
        self.slug = slug
        self.func = identity(slug)

    def __add__(self, other):
        return CombinedTimeSeries([self, other], operator.add, u'+', 6)

    def __sub__(self, other):
        return CombinedTimeSeries([self, other], operator.sub, u'−', 6)

    def __mul__(self, other):
        return CombinedTimeSeries([self, other], operator.mul, u'×', 8)

    def __div__(self, other):
        return CombinedTimeSeries([self, other], operator.truediv, u'÷', 8)
    __truediv__ = __div__

    def __neg__(self):
        return CombinedTimeSeries([self], operator.neg, display=u'-%s' % self.slug)

    def __unicode__(self):
        return self.slug

    def flatten(self, registry):
        ts = registry[self.slug]
        if ts.is_virtual:
            raise ReplaceMeWith(evaluate_equation(ts.equation, registry).timeseries)

    def get_slugs(self):
        return frozenset([self.slug])

    def get_evaluator(self):
        return identity(self.slug)

class Constant(object):
    def __init__(self, value):
        self.value = value
        self.tightness = 10
    def get_slugs(self):
        return frozenset()
    def flatten(self, registry):
        pass
    def get_evaluator(self):
        return lambda readings : self.value
    def __unicode__(self):
        return unicode(self.value)

class CombinedTimeSeries(TimeSeries):
    def __init__(self, operands, op, symbol=None, tightness=None):
        self.operands = operands
        self.op = op
        self.symbol, self.tightness = symbol, tightness

    def flatten(self, registry):
        operands, result = [], False
        for operand in self.operands:
            try:
                operand.flatten(registry)
            except ReplaceMeWith, e:
                operand, result = e.timeseries, True
            operands.append(operand)
        self.operands = operands
        return result

    def get_slugs(self):
        return frozenset(reduce(operator.or_, (o.get_slugs() for o in self.operands)))

    def __unicode__(self):
        symbol, tightness, operands = self.symbol, self.tightness, self.operands
        s = (' %s ' % symbol).join([('(%s)' if tightness > t.tightness else '%s') % unicode(t) for t in operands])
        if len(self.operands) == 1:
            s = self.symbol + s
        return s

    def get_evaluator(self):
        return combine(self.op, self.operands)


class Equation(dict):
    def __init__(self, timeseries, registry=None):
        self._timeseries = timeseries
        self.registry = registry or {}
        self.flattened = False

    def update_registry(self, slugs):
        slugs = set(slugs) - set(self.registry)
        if not slugs:
            return
        from .models import TimeSeries
        timeseries = list(TimeSeries.objects.filter(slug__in=slugs))
        if len(timeseries) != len(slugs):
            missing = slugs - set(ts.slug for ts in timeseries)
            raise NameError("Could not find time-series with slugs: %s" % ', '.join(missing))
        self.registry.update((ts.slug, ts) for ts in timeseries)

    def _get_slugs(self):
        return self._timeseries.get_slugs()
    def get_slugs(self):
        self.flatten()
        return self._timeseries.get_slugs()

    def _flatten(self):
        try:
            return self._timeseries.flatten(self.registry)
        except ReplaceMeWith, e:
            self._timeseries = e.timeseries
            return True

    def flatten(self):
        if self.flattened:
            return
        while True:
            self.update_registry(self._get_slugs())
            if not self._flatten():
                break
        self.flattened = True

    def get_evaluator(self):
        return self.timeseries.get_evaluator()

    @property
    def timeseries(self):
        self.flatten()
        return self._timeseries

def _quote(match):
    term = match.group(1)
    if term == '-':
        return '-'
    try:
        float(term)
    except (ValueError, TypeError):
        return "T('%s')" % term
    else:
        return "C(%s)" % term

def evaluate_equation(equation, registry=None):
    equation_string = re.sub(r'([a-zA-Z_.\d\-]+)', _quote, equation)
    if registry:
        equation_string = 'Equation(%s, registry)' % equation_string
    else:
        equation_string = 'Equation(%s)' % equation_string
    e_globals = {'__builtins__': {},
                 'T': TimeSeries,
                 'C': Constant,
                 'Equation': Equation,
                 'registry': registry}
    return eval(equation_string, e_globals)
