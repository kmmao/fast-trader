# -*- coding: utf-8 -*-

from fast_trader.utils import timeit, message2dict, int2datetime, attrdict

import os
import zmq
import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import sqlalchemy.orm.exc as orm_exc

from fast_trader.app.settings import settings, Session
from fast_trader.app.models import StrategyStatus


class StrategyProxy:

    def __init__(self, strategy_id):
        self._sock = zmq.Context().socket(zmq.REQ)
        self._sock.setsockopt(zmq.REQ_RELAXED, 1)

        url = f"tcp://{settings['strategy_host']}:{settings['strategy_port']}"
        print(url)
        self._sock.connect(url)

        self.strategy_id = strategy_id
        self._status = {}
        self.token = ''
        # self._running = False

    def _retrieve_status(self):
        try:
            session = Session()
            status = (
                session
                .query(StrategyStatus)
                .filter_by(strategy_id=self.strategy_id)
                .one()
            ).__dict__
            status.pop('_sa_instance_state')
        except orm_exc.NoResultFound:
            status = {}
        self._status = status

    def get_status(self):
        self._retrieve_status()
        return self._status

    def is_running(self):
        status = self.get_status()
        last_heartbeat = status.get('last_heartbeat', 0) or 0
        now = datetime.datetime.now().timestamp()
        # NOTE: 如果与策略服务端时间相差较大，则无法给出正确判断
        if now - last_heartbeat < 5 and status.get('running'):
            return True
        return False

    def send_request(self, request):
        # TODO: timeout
        self._sock.send_json(request)
        ret = self._sock.recv_json()
        return ret

    def update_settings(self, config):
        self.send_request({
            'api_name': 'update_settings',
            'settings': config
        })

    def start_strategy(self):
        """
        Returns
        ----------
        ret: bool
            策略启动成功返回`True`, 否则返回`False`
        """
        if self.is_running():
            return {'ret_code': 0, 'err_msg': '策略运行中，无需重复启动'}

        rsp = self.send_request({
            'strategy_id': self.strategy_id,
            'api_name': 'start_strategy',
            'kw': {},
        })
        if rsp['ret_code'] == 0:
            self.token = rsp['data']['token']
            self._status['running'] = True
        return rsp

    def stop_strategy(self):
        return self.send_request({
            'strategy_id': self.strategy_id,
            'api_name': 'stop_strategy',
            'kw': {},
        })

    def buy(self, code, price, quantity):
        return self.send_request({
            'strategy_id': self.strategy_id,
            'token': self.token,
            'api_name': 'buy',
            'kw': {
                'code': code,
                'price': price,
                'quantity': quantity,
            },
        })

    def sell(self, code, price, quantity):
        return self.send_request({
            'strategy_id': self.strategy_id,
            'token': self.token,
            'api_name': 'sell',
            'kw': {
                'code': code,
                'price': price,
                'quantity': quantity,
            },
        })

    def get_capital(self):
        return self.send_request({
            'strategy_id': self.strategy_id,
            'token': self.token,
            'api_name': 'get_capital',
            'kw': {},
        })

    def get_positions(self):
        return self.send_request({
            'strategy_id': self.strategy_id,
            'token': self.token,
            'api_name': 'get_account_positions',
            'kw': {},
        })

    def get_trades(self):
        return self.send_request({
            'strategy_id': self.strategy_id,
            'token': self.token,
            'api_name': 'get_trades',
            'kw': {},
        })

    def get_open_orders(self):
        return self.send_request({
            'strategy_id': self.strategy_id,
            'token': self.token,
            'api_name': 'get_open_orders',
            'kw': {},
        })


if __name__ == '__main__':
    from fast_trader.utils import get_mac_address
    p = StrategyProxy(1)
    p.update_settings({
        'ip': '192.168.211.169',
        'mac': get_mac_address(),
        'harddisk': '6B69DD46',
    })


