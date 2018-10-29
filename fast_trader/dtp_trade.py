# -*- coding: utf-8 -*-

import os
import zmq
import time
import random
import threading
import logging
from queue import Queue

from fast_trader.dtp import dtp_api_id
from fast_trader.dtp.dtp_api_id import *
from fast_trader.dtp import api_pb2 as dtp_struct
from fast_trader.dtp import type_pb2 as dtp_type

from fast_trader.dtp import ext_api_pb2 as dtp_struct_
from fast_trader.dtp import ext_type_pb2 as dtp_type_

from fast_trader.utils import timeit, message2dict, load_config, Mail
from fast_trader.logging import setup_logging


config = load_config()

REQUEST_TIMEOUT = 5


class Payload(object):

    def __init__(self, header, body):
        self.header = header
        self.body = body


def generate_request_id():
    return str(random.randrange(11000000, 11900000))


def generate_original_id():
    return str(random.randrange(61000000, 61900000))


class Dispatcher(object):

    def __init__(self, **kw):

        self._handlers = {}

        # 收件箱
        self._inbox = Queue()
        # 发件箱
        self._outbox = Queue()

        self._running = False
        self._run()

    def _run(self):

        self._running = True
        threading.Thread(target=self.process_inbox).start()
        threading.Thread(target=self.process_outbox).start()

    def process_inbox(self):

        while self._running:
            mail = self._inbox.get()
            self.dispatch(mail)

    def process_outbox(self):

        while self._running:
            mail = self._outbox.get()
            self.dispatch(mail)

    def bind(self, handler_id, handler, override=False):
        if not override and handler_id in self._handlers:
            raise KeyError(
                'handler {} already exists!')
        self._handlers[handler_id] = handler

    def put(self, mail):

        handler_id = mail['handler_id']

        if mail.get('sync'):
            return self.dispatch(mail)

        if handler_id.endswith('_req'):
            self._inbox.put(mail)
        elif handler_id.endswith('_rsp'):
            self._outbox.put(mail)
        else:
            raise Exception('Invalid message: {}'.format(mail))

    def dispatch(self, mail):
        return self._handlers[mail['handler_id']](mail)


class DTPType(object):

    api_map = {
        'CANCEL_REPORT': 'CancellationReport',
        'CANCEL_ORDER': 'CancelOrder',
        'CANCEL_RESPONSE': 'CancelResponse',
        'FILL_REPORT': 'FillReport',
        'LOGIN_ACCOUNT_REQUEST': 'LoginAccountRequest',
        'LOGIN_ACCOUNT_RESPONSE': 'LoginAccountResponse',
        'LOGOUT_ACCOUNT_REQUEST': 'LogoutAccountRequest',
        'LOGOUT_ACCOUNT_RESPONSE': 'LogoutAccountResponse',
        'PLACE_REPORT': 'PlacedReport',
        'PLACE_BATCH_ORDER': 'PlaceBatchOrder',
        'PLACE_BATCH_RESPONSE': 'PlaceBatchResponse',
        'PLACE_ORDER': 'PlaceOrder',
        'QUERY_CAPITAL_REQUEST': 'QueryCapitalRequest',
        'QUERY_CAPITAL_RESPONSE': 'QueryCapitalResponse',
        'QUERY_FILLS_REQUEST': 'QueryFillsRequest',
        'QUERY_FILLS_RESPONSE': 'QueryFillsResponse',
        'QUERY_ORDERS_REQUEST': 'QueryOrdersRequest',
        'QUERY_ORDERS_RESPONSE': 'QueryOrdersResponse',
        'QUERY_POSITION_REQUEST': 'QueryPositionRequest',
        'QUERY_POSITION_RESPONSE': 'QueryPositionResponse',
        'QUERY_RATION_REQUEST': 'QueryRationRequest',
        'QUERY_RATION_RESPONSE': 'QueryRationResponse'
    }

    proto_structs = {getattr(dtp_api_id, k): getattr(dtp_struct, v)
                     for k, v in api_map.items()}

    @classmethod
    def get_proto_type(cls, api_id):
        return cls.proto_structs[api_id]


class DTP(object):

    def __init__(self, dispatcher=None):

        self.dispatcher = dispatcher or Queue()

        self._account = config['account']
        self._ctx = zmq.Context()

        # 同步查询通道
        self._sync_req_resp_channel = self._ctx.socket(zmq.REQ)
        self._sync_req_resp_channel.connect(config['sync_channel_port'])

        # 异步查询通道
        self._async_req_channel = self._ctx.socket(zmq.DEALER)
        self._async_req_channel.connect(config['async_channel_port'])

        # 异步查询响应通道
        self._async_resp_channel = self._ctx.socket(zmq.SUB)
        self._async_resp_channel.connect(config['rsp_channel_port'])
        self._async_resp_channel.subscribe('{}'.format(self._account))

        # 风控推送通道
        self._risk_report_channel = self._ctx.socket(zmq.SUB)
        self._risk_report_channel.connect(config['risk_channel_port'])
        self._async_resp_channel.subscribe('{}'.format(self._account))

        self.logger = logging.getLogger('fast_trader.dtp_trade.DTP')

        self.start()

    def start(self):

        self._running = True
        threading.Thread(target=self.handle_counter_response).start()
        threading.Thread(target=self.handle_compliance_report).start()

    def _assign(self, cmsg, attrs):
        for attr, value in attrs.items():
            name = attr
            if attr == 'account':
                name = 'account_no'
            elif attr not in ['password', 'exchange', 'order_exchange_id',
                           'code', 'price', 'quantity', 'order_side',
                           'order_type', 'order_list']:
                continue
            if isinstance(value, list):
                repeated = getattr(cmsg, attr)
                for i in value:
                    item = repeated.add()
                    self._assign(item, i)
            else:
                setattr(cmsg, name, value)

    def handle_sync_request(self, mail):

        header = dtp_struct.RequestHeader()
        header.request_id = generate_request_id()
        header.api_id = mail['api_id']

        token = mail.get('token')
        if token:
            header.token = mail['token']

        req_type = DTPType.get_proto_type(mail['api_id'])

        body = req_type()

#        for k, v in mail._kw.items():
#            name = k
#            if k == 'account':
#                name = 'account_no'
#            elif k not in ['password', 'exchange', 'order_exchange_id']:
#                continue
#
#            setattr(body, name, mail[k])

        self._assign(body, mail._kw)

        self.logger.warning('account_no: {}'.format(body.account_no))

        payload = Payload(header, body)

        self._handle_sync_request(payload)

        return self._handle_sync_response(sync=mail['sync'])

    def handle_async_request(self, mail):

        header = dtp_struct.RequestHeader()
        header.request_id = generate_request_id()
        header.api_id = mail['api_id']
        header.token = mail['token']

        req_type = DTPType.get_proto_type(header.api_id)
        body = req_type()

        self._assign(body, mail._kw)

        payload = Payload(header, body)

        self._async_req_channel.send(
            payload.header.SerializeToString(), zmq.SNDMORE)

        self._async_req_channel.send(
            payload.body.SerializeToString())

    def handle_counter_response(self):

        sock = self._async_resp_channel

        while self._running:

            topic = sock.recv()
            report_header = sock.recv()
            report_body = sock.recv()

            self.logger.warning('topic: {}'.format(topic))
            header = dtp_struct.ReportHeader()
            header.ParseFromString(report_header)

            rsp_type = DTPType.get_proto_type(header.api_id)

            try:
                body = rsp_type()
                body.ParseFromString(report_body)
            except Exception:
                self.logger.warning('未知响应 api_id={}, {}'.format(
                    header.api_id, header.message))
                continue

            self.dispatcher.put(Mail(
                api_id=header.api_id,
                api_type='rsp',
                content=Payload(header, body)
            ))

    def handle_compliance_report(self):
        """
        风控消息推送
        """
        sock = self._risk_report_channel

        while self._running:

            topic = sock.recv()
            report_header = sock.recv()
            report_body = sock.recv()

            header = dtp_struct.ReportHeader()
            header.ParseFromString(report_header)
            self.logger.warning('风控消息 {}', header.api_id, header.message)

            body = dtp_struct.PlacedReport()
            body.ParseFromString(report_body)


    def _handle_sync_request(self, payload):
        try:
            self._sync_req_resp_channel.send(
                payload.header.SerializeToString(), zmq.SNDMORE)

            self._sync_req_resp_channel.send(
                payload.body.SerializeToString())
        except zmq.ZMQError as e:
            self.logger.error('查询响应中...', exc_info=True)

    def _handle_sync_response(self, sync=False):

        waited_time = 0

        while waited_time < REQUEST_TIMEOUT:

            try:
                _header = self._sync_req_resp_channel.recv(flags=zmq.NOBLOCK)
                _body = self._sync_req_resp_channel.recv(flags=zmq.NOBLOCK)

            except zmq.ZMQError as e:
                time.sleep(0.1)
                waited_time += 0.1

            else:
                response_header = dtp_struct.ResponseHeader()
                response_header.ParseFromString(_header)

                api_id = response_header.api_id
                rsp_type = DTPType.get_proto_type(api_id)

                response_body = rsp_type()
                response_body.ParseFromString(_body)
                payload = Payload(response_header, response_body)

                mail = Mail(
                    api_id=api_id,
                    api_type='rsp',
                    sync=sync,
                    content=payload
                )

                if sync:
                    return mail

                return self.dispatcher.put(mail)

        self.logger.error('{} 查询超时'.format(resp_type))


class Order(object):

    exchange = dtp_type.EXCHANGE_SH_A
    code = ''
    price = ''
    quantity = 0
    order_side = dtp_type.ORDER_SIDE_BUY
    order_type = dtp_type.ORDER_TYPE_LIMIT

    def __getitem__(self, key):
        return getattr(self, key)


class Trader(object):

    def __init__(self, dispatcher=None, broker=None):

        self.dispatcher = dispatcher
        self.broker = broker

        self._account = ''
        self._token = ''
        self._logined = False

        self._position_results = []
        self._trade_results = []
        self._order_results = []

        self._strategies = []

        setup_logging()
        self.logger = logging.getLogger('fast_trader.dtp_trade.Trader')
        self.logger.info('初始化 process_id={}'.format(os.getpid()))

    def start(self):

        if self.dispatcher is None:
            self.dispatcher = Dispatcher()

        if self.broker is None:
            self.broker = DTP(self.dispatcher)

        self.bind()

    def bind(self):

        dispatcher, broker = self.dispatcher, self.broker

        for api_id in RSP_API_NAMES:
            dispatcher.bind('{}_rsp'.format(api_id), self._on_response)

        for api_id in REQ_API_NAMES:
            api_name = REQ_API_NAMES[api_id]
            handler = getattr(broker, api_name)
            dispatcher.bind('{}_req'.format(api_id), handler)

    def add_strategy(self, strategy):
        self._strategies.append(strategy)

    def _on_response(self, mail):

        self.logger.info(message2dict(mail['content'].header))
        self.logger.info(message2dict(mail['content'].body))

        api_id = mail['api_id']
        response = mail['content']
        msg = message2dict(response.body)

        if api_id == LOGIN_ACCOUNT_RESPONSE:
            self.on_login(msg)
        elif api_id == LOGOUT_ACCOUNT_RESPONSE:
            self.on_logout(msg)
        else:
            for ea in self._strategies:
                getattr(ea, RSP_API_NAMES[api_id])(msg)

    @property
    def account_no(self):
        return self._account

    @property
    def logined(self):
        return self._logined

    def on_login(self, msg):
        try:
            self._token = msg['token']
        except Exception as e:
            self.logger.error('登录失败', exc_info=True)
            raise e

        self.logger.info('登入账户 {}, {}'.format(self.account_no, msg))

    def on_logout(self, mail):
        self.logger.info('登出账户 {}'.format(self.account_no))


    def login(self, account, password, sync=True):

        self._account = account
        ret = self.login_account(account=account, password=password, sync=True)

        if sync:

            payload = ret['content']
            login_msg = payload.header.message

            if payload.header.code == dtp_type.RESPONSE_CODE_OK:
                self._logined = True
                self._token = payload.body.token

                self.logger.info(
                    '登录成功 <{}> {}'.format(self.account_no, login_msg))

            else:
                self.logger.info(
                    '登录失败 <{}> {}'.format(self.account_no, login_msg))

            return ret

        timeout = 0
        while True:
            if self._token != '':
                break
            time.sleep(0.5)
            timeout += 0.5
            if timeout > REQUEST_TIMEOUT:
                self.logger.error('登录超时')
                raise Exception('登录超时!')


    def logout(self, **kw):
        mail = Mail(
            api_type='req',
            api_id=LOGOUT_ACCOUNT_REQUEST,
            account=self._account,
            token=self._token
        )
        self.dispatcher.put(mail)

    def login_account(self, **kw):
        mail = Mail(
            api_type='req',
            api_id=LOGIN_ACCOUNT_REQUEST,
            **kw
        )
        return self.dispatcher.put(mail)

    def send_order(self, order_original_id, exchange,
                   code, price, quantity, order_side,
                   order_type=dtp_type.ORDER_TYPE_LIMIT):
        """
        报单委托
        """
        mail = Mail(
            api_type='req',
            api_id=PLACE_ORDER,
            account=self._account,
            token=self._token,
            order_original_id=order_original_id,
            exchange=exchange,
            code=code,
            price=price,
            quantity=quantity,
            order_side=order_side,
            order_type=order_type
        )
        self.dispatcher.put(mail)

        self.logger.info(('报单委托 account={}, code={}, price={}, quantity={},'
                         + ' order_side={}, order_type={}').format(
                         self.account_no, code, price, quantity, order_side,
                         order_type))

    def place_order(self, order):

        mail = Mail(
            api_type='req',
            api_id=PLACE_ORDER,
            account=self._account,
            token=self._token,
            exchange=order.exchange,
            code=order.code,
            price=order.price,
            quantity=order.quantity,
            order_side=order.order_side,
            order_type=order.order_type
        )
        self.dispatcher.put(mail)

    def place_order_batch(self, orders):
        """
        批量下单
        """
        mail = Mail(
            api_type='req',
            api_id=PLACE_BATCH_ORDER,
            account=self._account,
            token=self._token,
            order_list=orders
        )
        self.dispatcher.put(mail)
        self.logger.info('批量买入委托 {}'.format(mail))

    def cancel_order(self, **kw):
        """
        撤单
        """
        mail = Mail(
            api_type='req',
            api_id=CANCEL_ORDER,
            account=self._account,
            token=self._token,
            exchange=kw['exchange'],
            order_exchange_id=kw['order_exchange_id']
        )
        self.dispatcher.put(mail)

    def query_orders(self, **kw):
        """
        查询订单
        """
        mail = Mail(
            api_type='req',
            api_id=QUERY_ORDERS_REQUEST,
            sync=kw.get('sync', False),
            account=self._account,
            token=self._token
        )
        return self.dispatcher.put(mail)

    def query_trades(self, **kw):
        """
        查询成交
        """
        mail = Mail(
            api_type='req',
            api_id=QUERY_FILLS_REQUEST,
            sync=kw.get('sync', False),
            account=self._account,
            token=self._token
        )
        return self.dispatcher.put(mail)

    def query_positions(self, **kw):
        """
        查询持仓
        """
        mail = Mail(
            api_type='req',
            api_id=QUERY_POSITION_REQUEST,
            sync=kw.get('sync', False),
            account=self._account,
            token=self._token
        )
        return self.dispatcher.put(mail)

    def query_capital(self, **kw):
        """
        查询账户资金
        """
        mail = Mail(
            api_type='req',
            api_id=QUERY_CAPITAL_REQUEST,
            sync=kw.get('sync', False),
            account=self._account,
            token=self._token
        )
        return self.dispatcher.put(mail)

    def query_ration(self, **kw):
        """
        查询配售权益
        """
        mail = Mail(
            api_type='req',
            api_id=QUERY_RATION_REQUEST,
            sync=kw.get('sync', False),
            account=self._account,
            token=self._token
        )
        return self.dispatcher.put(mail)


class PositionDetail(object):
    """
    交易标的持仓状态
    """

    # 代码
    code = ''
    # 交易所
    exchange = ''
    # 当前持仓
    position = 0
    # 昨日最终持仓
    yd_last_position = 0
    # 昨日持仓均价
    yd_avg_price = 0

    def __init__(self):
        pass


class AccountDetail(object):
    """
    账户详情
    """
    # 账号
    account_no = ''
    # 当前权益
    current_capital = 0.
    # 现金余额
    balance = 0.


if __name__ == '__main__':

    trader = Trader()
    trader.start()
    trader.login(**config)
