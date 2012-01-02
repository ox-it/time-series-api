# -*- coding: UTF-8
from __future__ import division

import random
import unittest

import mock

from openorg_timeseries import combine, models


class CombineTestCase(unittest.TestCase):
    test_timeseries = {# This one is fairly simple
                       'v1': {'equation': 'a + b / c',
                              'func': lambda r: r['a'] + r['b'] / r['c']},
                       # This one is a virtual series containing just the above
                       'v2': {'equation': 'v1',
                              'func': lambda r: r['a'] + r['b'] / r['c']},
                       'v3': {'equation': 'v1 + v2 * 4 - a / d',
                              'func': lambda r: 5 * (r['a'] + r['b'] / r['c']) - r['a'] / r['d']}}

    def getTimeSeriesFilterReturnValue(self, filter=None, include_virtual=False):
        class TimeSeries(object):
            is_virtual = False
            def __init__(self, slug, equation=None):
                self.slug, self.equation = slug, equation
        class VirtualTimeSeries(TimeSeries):
            is_virtual = True
        tss = []
        for i in range(ord('a'), ord('z') + 1):
            slug = chr(i)
            tss.append(TimeSeries(slug=slug))
        if include_virtual:
            for i in range(1, 3):
                slug = 'v%d' % i
                tss.append(VirtualTimeSeries(slug=slug,
                                             equation=self.test_timeseries[slug]['equation']))
        if filter:
            tss = [ts for ts in tss if ts.slug in filter]
        return tss


    def getTestReadings(self):
        readings = {}
        for i in range(ord('a'), ord('z') + 1):
            readings[chr(i)] = random.randrange(1, 100)
        return readings


    def testEvaluate(self):
        for timeseries in self.test_timeseries.itervalues():
            combine.evaluate_equation(timeseries['equation'])

    def testSimpleWithMissing(self):
        timeseries = self.test_timeseries['v1']
        equation = combine.evaluate_equation(timeseries['equation'])

        self.assertRaises(NameError, equation.get_evaluator)

    @mock.patch('openorg_timeseries.models.TimeSeries')
    def testSimple(self, timeseries_model):
        timeseries_model.objects.filter.return_value = self.getTimeSeriesFilterReturnValue(['a', 'b', 'c'])
        timeseries = self.test_timeseries['v1']
        equation = combine.evaluate_equation(timeseries['equation'])

        evaluator = equation.get_evaluator()
        self.assertEqual(equation.get_slugs(), set('abc'))

        timeseries_model.objects.filter.assert_called_once_with(slug__in=set(['a', 'b', 'c']))
        timeseries_model.objects.filter.reset_mock()

        for i in range(10):
            readings = self.getTestReadings()
            self.assertEqual(evaluator(readings),
                             timeseries['func'](readings))
            self.assertFalse(timeseries_model.objects.filter.called)


    def testVirtualWithout(self):
        timeseries = self.test_timeseries['v2']
        equation = combine.evaluate_equation(timeseries['equation'])

        self.assertRaises(NameError, equation.get_evaluator)

    @mock.patch('openorg_timeseries.models.TimeSeries')
    def testVirtualFlatten(self, timeseries_model):
        def side_effect(slug__in):
            return self.getTimeSeriesFilterReturnValue(slug__in, include_virtual=True)
        timeseries_model.objects.filter.side_effect = side_effect
        timeseries = self.test_timeseries['v2']
        equation = combine.evaluate_equation(timeseries['equation'])

        self.assertEqual(unicode(equation._timeseries), 'v1')
        equation.flatten()
        self.assertEqual(unicode(equation._timeseries), u'a + b ÷ c')

        self.assertEqual(timeseries_model.objects.filter.call_args_list,
                         [((), {'slug__in': set(['v1'])}),
                          ((), {'slug__in': set(['a', 'b', 'c'])})])

        timeseries_model.objects.filter.reset_mock()

        evaluator = equation.get_evaluator()
        self.assertEqual(equation.get_slugs(), set('abc'))

        for i in range(10):
            readings = self.getTestReadings()
            self.assertEqual(evaluator(readings),
                             timeseries['func'](readings))
            self.assertFalse(timeseries_model.objects.filter.called)

    @mock.patch('openorg_timeseries.models.TimeSeries')
    def testVirtualNested(self, timeseries_model):
        def side_effect(slug__in):
            return self.getTimeSeriesFilterReturnValue(slug__in, include_virtual=True)
        timeseries_model.objects.filter.side_effect = side_effect
        timeseries = self.test_timeseries['v3']

        equation = combine.evaluate_equation(timeseries['equation'])

        evaluator = equation.get_evaluator()
        self.assertEqual(equation.get_slugs(), set('abcd'))

        for i in range(10):
            readings = self.getTestReadings()
            self.assertEqual(evaluator(readings),
                             timeseries['func'](readings))



