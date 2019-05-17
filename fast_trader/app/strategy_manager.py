# -*- coding: utf-8 -*-

import os

import datetime
import time
import zmq
import logging
import importlib
import threading
import subprocess
import psutil
import collections
import functools

import sqlalchemy.orm.exc as orm_exc

from fast_trader.settings import settings, Session, SqlLogHandler
from fast_trader.strategy import Strategy, StrategyFactory
from fast_trader.models import StrategyStatus, StrategyServerModel
from fast_trader.utils import timeit, get_current_ts

from fast_trader.dtp_trade import (OrderResponse, TradeResponse,
                                   CancellationResponse,
                                   QueryOrderResponse, QueryTradeResponse,
                                   QueryPositionResponse)

from fast_trader import rest_api


SERVER_TIMEOUT_SECS = 3


class StrategyNotFound(Exception):
    pass


class Manager:

    def __init__(self):
        self._ctx = zmq.Context()
        self._sock = self._ctx.socket(zmq.REP)
        host = settings['strategy_manager_host']
        port = settings['strategy_manager_port']
        # xx
        port = 5700
        conn = f"tcp://{host}:{port}"
        self._sock.bind(conn)

        self._update_server_status()

        self._load_strategy_settings()
        self._factory = None
        self._strategies = {}

        # 缓存历史资金查询记录
        self._capital_records = collections.defaultdict(list)

        self._heartbeat_thread = threading.Thread(target=self.send_heartbeat)

        self.logger = logging.getLogger('strategy_manager')
        self.logger.addHandler(SqlLogHandler())

    def _load_strategy_settings(self):
        if settings['use_rest_api'] is True:
            self._strategy_settings = rest_api.user_meta.copy()
            self._strategy_settings.pop('token')
        else:
            self._strategy_settings = None

    def _update_server_status(self):
        now = datetime.datetime.now()
        now_str = now.strftime(
                '%Y%m%d %H:%M:%S.%f')
        ts = get_current_ts()
        msg = {
            'pid': os.getpid(),
            'start_time': now_str,
            'last_heartbeat': ts
        }
        session = Session()
        last_status = (
            session
            .query(StrategyServerModel)
            .filter_by(id=1)
            .first()
        )
        if last_status is None:
            status = StrategyServerModel.from_msg(msg)
            session.add(status)
        else:
            for k, v in msg.items():
                setattr(last_status, k, v)

        session.commit()
        session.close()

    def receive(self):
        return self._sock.recv_json()  # zmq.NOBLOCK)

    def send(self, msg):
        self._sock.send_json(msg)

    def send_heartbeat(self):
        session = Session()
        while True:
            time.sleep(1)
            ts = get_current_ts()

            (
                session
                .query(StrategyServerModel)
                .filter_by(id=1)
                .update({'last_heartbeat': ts}))

            session.commit()

    @property
    def factory(self):
        if self._strategy_settings is None:
            raise Exception('交易参数未配置')
        if self._factory is None:
            self._factory = StrategyFactory(
                factory_settings=self._strategy_settings)
            self._factory.dtp.logger.addHandler(SqlLogHandler())
        return self._factory

    def update_settings(self, strategy_settings):
        self._strategy_settings = strategy_settings
        return {'ret_code': 0, 'data': None}

    def get_accounts(self):
        return rest_api.get_accounts()

    def get_capital(self, account_no):
        cap = rest_api.query_capital(account_no=account_no)
        self._capital_records[account_no].append(cap)
        return cap

    def get_history_capital_records(self, account_no):
        return self._capital_records[account_no]

    def add_strategy(self, strategy):
        k = (strategy.account_no, strategy.strategy_id)
        self._strategies[k] = strategy

    def get_strategy(self, account_no, strategy_id):
        try:
            return self._strategies[(account_no, strategy_id)]
        except KeyError:
            raise StrategyNotFound('策略未启动')

    @staticmethod
    def get_strategy_list():
        loader = StrategyLoader()
        strategies = loader.load()

        ret = []
        session = Session()
        for s in strategies:
            res = (
                session
                .query(StrategyStatus)
                .filter_by(strategy_id=s.strategy_id)
                .all())
            if res:
                ea = res[0]
                ret.append({
                    'strategy_name': ea.strategy_name,
                    'strategy_id': ea.strategy_id,
                    'running': ea.is_running(),
                    'start_time': ea.start_time or ''
                })
            else:
                ret.append({
                    'strategy_name': s.strategy_name,
                    'strategy_id': s.strategy_id,
                    'running': False,
                    'start_time': ''})
        session.close()
        return ret

    def start_strategy(self, strategy_id, account_no):
        try:
            if (account_no, strategy_id) in self._strategies:
                strategy = self.get_strategy(account_no, strategy_id)
                # return {'ret_code': -1, 'err_msg': 'Already running'}
            else:
                strategy = self.instantiate_strategy(account_no, strategy_id)

                ret = strategy.start()
                if ret['ret_code'] != 0:
                    strategy.remove_self()
                    return ret

                self.add_strategy(strategy)

            return {'ret_code': 0, 'data': {'token': strategy.trader._token}}

        except Exception as e:
            return {'ret_code': -1, 'err_msg': repr(e)}

    def stop_strategy(self, strategy_id, account_no):
        # FIXME: by acc
        try:
            strategy = self.get_strategy(account_no, strategy_id)
            strategy.stop()
            self._strategies.pop(strategy_id)

            session = Session()
            (
                session
                .query(StrategyStatus)
                .filter_by(strategy_id=strategy_id)
                .update({'running': False})
            )
            session.commit()
            session.close()

            return {'ret_code': 0, 'data': None}
        except Exception as e:
            return {'ret_code': -1, 'err_msg': repr(e)}

    def remove_strategy(self, strategy_id):
        # TODO: 删除策略前须保持策略无任何持仓
        # 并释放掉所有分配给该策略的资源
        raise NotImplementedError('暂不支持删除策略')

    def _get_all_pages(self, handle):
        offset = 0
        size = 200
        all_objs = []
        while True:

            mail = handle(request_id='',
                          sync=True,
                          pagination={
                              'size': size,
                              'offset': offset
                          })

            list_name = ''
            for attr in ['order_list', 'fill_list', 'position_list']:
                if hasattr(mail.body, attr):
                    list_name = attr
                    break

            _objs = mail['body'].get(list_name, [])

            all_objs.extend(_objs)
            if len(_objs) < size:
                break
            offset = mail.body.pagination.offset

        return all_objs

    def get_positions(self, account_no):
        handle = functools.partial(
            rest_api.restapi_query_positions,
            trader=None)
        positions = self._get_all_pages(handle)
        ret = [QueryPositionResponse.from_msg(pos) for pos in positions]
        return ret

    def get_orders(self, account_no):
        handle = functools.partial(
            rest_api.restapi_query_orders,
            trader=None)
        orders = self._get_all_pages(handle)
        ret = [QueryOrderResponse.from_msg(order) for order in orders]
        return ret

    def get_trades(self, account_no):
        handle = functools.partial(
            rest_api.restapi_query_fills,
            trader=None)
        trades = self._get_all_pages(handle)
        ret = [QueryTradeResponse.from_msg(trade) for trade in trades]
        return ret

    def get_strategy_positions(self, account_no, strategy_id):
        strategy = self.get_strategy(account_no, strategy_id)
        ret = strategy.get_positions()
        return ret

    def get_strategy_orders(self, account_no, strategy_id):
        strategy = self.get_strategy(account_no, strategy_id)
        ret = strategy.get_orders()
        return ret

    def get_strategy_trades(self, account_no, strategy_id):
        strategy = self.get_strategy(account_no, strategy_id)
        ret = strategy.get_trades()
        return ret

    def get_traded_amount(self, account_no):
        # FIXME:
        ret = {
            'buy_amount': 600000.,
            'sell_amount': 400000.
        }
        return ret

    def get_strategy_traded_amount(self, account_no, strategy_id):
        # FIXME:
        ret = {
            'buy_amount': 70000.,
            'sell_amount': 30000.
        }
        return ret

    def handle_request(self, request):

        try:
            api_name = request['api_name']

            if api_name == 'get_accounts':
                return self.get_accounts(**request['kw'])

            elif api_name == 'get_capital':
                return self.get_capital(**request['kw'])

            elif api_name == 'get_history_capital_records':
                return self.get_history_capital_records(**request['kw'])

            elif api_name == 'start_strategy':
                return self.start_strategy(**request['kw'])

            elif api_name == 'stop_strategy':
                return self.stop_strategy(**request['kw'])

            elif api_name == 'update_settings':
                return self.update_settings(**request['kw'])

            elif api_name == 'get_strategy_list':
                return self.get_strategy_list(**request['kw'])

            elif api_name == 'get_positions':
                return self.get_positions(**request['kw'])

            elif api_name == 'get_orders':
                return self.get_orders(**request['kw'])

            elif api_name == 'get_trades':
                return self.get_trades(**request['kw'])

            elif api_name == 'get_strategy_orders':
                return self.get_strategy_orders(**request['kw'])

            elif api_name == 'get_strategy_trades':
                return self.get_strategy_trades(**request['kw'])

            elif api_name == 'get_strategy_traded_amount':
                return self.get_strategy_traded_amount(**request['kw'])

            elif api_name == 'get_traded_amount':
                return self.get_traded_amount(**request['kw'])

            else:
                return RuntimeError('未知接口: {api_name}')
        except Exception as e:
            self.logger.error(f'Request failed: request={request}',
                              exc_info=True)
            return {'ret_code': -1, 'err_msg': repr(e)}

    def instantiate_strategy(self, account_no, strategy_id):
        ss = StrategyLoader().load()
        try:
            StrategyCls = next(
                filter(lambda x: x.strategy_id == strategy_id, ss))
        except StopIteration:
            raise RuntimeError(f'策略读取失败，strategy_id: {strategy_id}')

        strategy = self.factory.generate_strategy(
            StrategyCls,
            strategy_id=strategy_id,
            account_no=account_no,
        )
        return strategy

    def close(self):
        self._ctx.destroy()

    def run(self):

        self.logger.info(f'Strategy manager started. Pid={os.getpid()}')

        self._heartbeat_thread.start()

        while True:
            # 监听外部指令
            try:
                request = self.receive()
            except zmq.Again:
                pass
            else:
                self.logger.info(f'received: {request}')
                ret = self.handle_request(request)
                self.send(ret)
                self.logger.info(f'sent: {ret}')


class StrategyLoader:

    def __init__(self):
        self.strategy_suffix = '.py'
        self.strategy_dir = \
            settings['strategy_directory']

    def load(self):
        strategy_classes = []

        if self.strategy_dir == '':
            strategy_dir = os.path.dirname(__file__)
        else:
            strategy_dir = self.strategy_dir

        for fl in os.listdir(strategy_dir):
            if not fl.endswith(self.strategy_suffix):
                continue
            path = os.path.join(strategy_dir, fl)
            spec = importlib.util.spec_from_file_location(
                "strategy", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            for name in dir(mod):
                el = getattr(mod, name)
                if isinstance(el, type):
                    if issubclass(el, Strategy) and el is not Strategy:
                        strategy_classes.append(el)
        return strategy_classes


def main():
    manager = Manager()
    manager.run()


class StrategyServer:

    def __init__(self):
        self.server_id = 1
        self.proc = None
        self._pid = None
        self.logger = logging.getLogger('strategy_server')

    def start(self):
        if self.is_running():
            self.logger.warning('strategy server正在运行中, 无需重复启动')
            return

        self.proc = subprocess.Popen(
            ['python', __file__],
            shell=False,
            bufsize=1,
            universal_newlines=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.logger.info(f'strategy server已启动, pid={os.getpid()}')

    def stop(self):
        session = Session()
        try:
            pid = session.query(StrategyServerModel.pid).one()[0]
        except orm_exc.NoResultFound:
            self.logger.warn('strategy server未找到运行记录')
        else:
            for proc in psutil.process_iter():
                if proc.pid == pid:
                    proc.kill()
                    break

        # 更新所有策略状态
        for stats in session.query(StrategyStatus).all():
            stats.running = False
        session.commit()
        session.close()

        self.logger.info('strategy server已停止')

    def restart(self):
        self.stop()
        time.sleep(2)
        self.start()
        time.sleep(2)

    def is_running(self):
        session = Session()
        res = (
            session
            .query(StrategyServerModel)
            .filter_by(id=self.server_id)
            .all())
        if res:
            last_ts = res[0].last_heartbeat
            self._pid = res[0].pid
            current_ts = get_current_ts()
            if current_ts < last_ts + SERVER_TIMEOUT_SECS:
                return True
        return False


if __name__ == '__main__':
    # Do not edit!
    # main()
    pass
