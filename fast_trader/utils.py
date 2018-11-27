# -*- coding: utf-8 -*-

import os
import time
import datetime
import functools
import math

from google.protobuf.message import Message
from google.protobuf.pyext._message import RepeatedCompositeContainer
import yaml


class attrdict(dict):

    __slots__ = ()

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as e:
            raise AttributeError(key)

    def __setattr__(self, key, value):
        raise AttributeError('Assignment not allowed')


class Mail(attrdict):

    def __init__(self, api_type, api_id, **kw):

        if api_type == 'req':
            assert 'request_id' in kw

        if 'handler_id' not in kw:
            kw['handler_id'] = '{}_{}'.format(api_id, api_type)

        if 'sync' not in kw:
            kw['sync'] = False

        if 'ret_code' not in kw:
            kw['ret_code'] = 0

        kw.update({
            'api_type': api_type,
            'api_id': api_id
        })

        self.update(kw)


def timeit(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        t0_ = time.time()
        ret = func(*args, **kwargs)
        print('%s in %.6f secs' % (
            func.__name__, time.time() - t0_))
        return ret
    return wrapper


def load_config(path=None):
    if path is None:
        dirname = os.path.dirname(__file__)
        path = os.path.join(dirname, 'config.yaml')
    with open(path, 'r') as f:
        conf = yaml.load(f)
    return conf


def message2dict(msg, including_default_value_fields=True):
    """
    Convert protobuf message to dict
    """

    dct = attrdict()

    if isinstance(msg, Message):

        for field in msg.DESCRIPTOR.fields:
            name = field.name
            dct[name] = message2dict(getattr(msg, name))

        return dct

    elif isinstance(msg, RepeatedCompositeContainer):
        return list(map(message2dict, msg))

    else:
        return msg


def message2tuple(msg, kind):
    """
    Convert protobuf message to namedtuple
    Doesn't support nested messages
    """

    dct = {}

    for field in msg.DESCRIPTOR.fields:
        name = field.name
        dct[name] = getattr(msg, name)

    ret = kind(**dct)

    return ret


def int2datetime(n_date=None, n_time=None, utc=False):
    if n_date is None and n_time is None:
        raise ValueError
    elif n_date and n_time is None:
        dt = datetime.datetime.strptime('{}'.format(n_date), '%Y%m%d')
    elif n_date is None and n_time:
        dt = datetime.datetime.strptime('{}'.format(n_time), '%H%M%S%f').time()
    else:
        dt = datetime.datetime.strptime(
            '{}{}'.format(n_date, n_time),
            '%Y%m%d%H%M%S%f')
    if utc:
        return dt.astimezone(datetime.timezone.utc)
    return dt


class _IDPool:
    """
    为每个不同的trader与strategy实例组合分配不同的id段
    """
    def __init__(self, max_int=2147483647,
                 max_traders=10,
                 max_strategies_per_trader=10):
        self.max_int = max_int
        self.max_traders = max_traders
        self.max_strategies_per_trader = max_strategies_per_trader

        self.trader_reserves = {}
        self.strategy_reserves = {}
        self.strategy_ranges = {}
        self.slice()

    def time_trim(func):
        @functools.wraps(func)
        def wrapper(*args, **kw):
            def trim(rng):
                rng_len = len(rng)
                now = datetime.datetime.now()
                midnight = datetime.datetime(*now.timetuple()[:3])
                checkpoint = (now - midnight).seconds / 86400
                expired = math.ceil(checkpoint * rng_len)
                return rng[expired+1:]
            ret = func(*args, **kw)
            if isinstance(ret, range):
                return trim(ret)
            elif isinstance(ret, dict):
                return {k: trim(v) for k, v in ret.items()}
            else:
                raise TypeError
        return wrapper

    @staticmethod
    def slice_range(rng, n):
        # reserve some values
        reserve_cnt = 1000 + len(rng) % n
        new_rng = rng[:-reserve_cnt]
        reserve = rng[-reserve_cnt:]
        range_len = int(len(new_rng) / n)

        if range_len < reserve_cnt:
            raise ValueError('Range to narrow')

        ranges = []
        i = 0
        for _ in new_rng[::range_len]:
            j = i + range_len
            ranges.append(new_rng[i:j])
            i = j

        return ranges, reserve

    def slice(self):

        trader_ranges, sys_reserve = self.slice_range(
            range(1, self.max_int + 1), self.max_traders)

        self.trader_ranges = {i: v for i, v in enumerate(trader_ranges)}
        self.sys_reserve = sys_reserve

    def get_strategy_ranges_and_reserves(self, trader_id):
        trader_range = self.trader_ranges[trader_id]

        ranges, reserve = self.slice_range(
            trader_range, self.max_strategies_per_trader)

        strategy_ranges = {(trader_id, i): v[:-1000]
                           for i, v in enumerate(ranges)}
        strategy_reserves = {(trader_id, i): v[-1000:]
                             for i, v in enumerate(ranges)}

        self.strategy_ranges.update(strategy_ranges)
        self.strategy_reserves.update(strategy_reserves)
        self.trader_reserves[trader_id] = reserve

        return strategy_ranges, strategy_reserves

    def get_strategy_whole_ranges(self, trader_id):
        return self.get_strategy_ranges_and_reserves(trader_id)[0]

    @time_trim
    def get_strategy_ranges(self, trader_id):
        return self.get_strategy_ranges_and_reserves(trader_id)[0]

    @time_trim
    def get_strategy_range(self, trader_id, strategy_id):
        if (trader_id, strategy_id) not in self.strategy_ranges:
            self.get_strategy_ranges_and_reserves(trader_id)
        return self.strategy_ranges[trader_id, strategy_id]

    @time_trim
    def get_strategy_reserve(self, trader_id, strategy_id):
        if (trader_id, strategy_id) not in self.strategy_reserves:
            self.get_strategy_ranges_and_reserves(trader_id)
        return self.strategy_reserves[trader_id, strategy_id]

    @time_trim
    def get_trader_reserve(self, trader_id):
        if trader_id not in self.trader_reserves:
            self.get_strategy_ranges_and_reserves(trader_id)
        return self.trader_reserves[trader_id]

    @time_trim
    def get_sys_reserve(self):
        return self.sys_reserve

# TODO: configurable
_id_pool = _IDPool()
