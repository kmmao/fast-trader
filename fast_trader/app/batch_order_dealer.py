# -*- coding: utf-8 -*-

import time, datetime
from collections import defaultdict

from fast_trader.dtp import dtp_api_id
from fast_trader.dtp import type_pb2 as dtp_type
from fast_trader.dtp_quote import TradeFeed, OrderFeed, TickFeed
from fast_trader.strategy import Strategy, StrategyFactory, to_timeint
from fast_trader.utils import timeit, message2dict, int2datetime, attrdict

from fast_trader.app.models import *


def get_target_trading_amount():
    return {
        '600056.SH': 10000.,
        '601555.SH': 10000.,
        '601668.SH': 10000.,
    }


class BatchOrderDealer(Strategy):
    """
    1.
        1.1 最新价加滑点(slippages)报单
        1.2 报单周期(order_period)结束后，撤单已涨跌停价报单
    2.
        最新价已达到涨跌停，直接以涨跌停价格报单
    """

    @property
    def now(self):
        return datetime.datetime.now()

    @property
    def holdings(self):
        positions = self.get_positions()
        return [p['code'] for p in positions]

    def on_start(self):
        """
        执行策略启动前的一些初始化操作
        """

        # 是否允许报单
        self.allow_trading = False

        # 目标持仓金额
        self.target_amount = get_target_trading_amount()

        self.target_codes = list(self.target_amount.keys())

        # 委托单占用的现金
        self.pending_amount = defaultdict(lambda: 0.)
        # 待成交金额（不包含委托占用资金）
        self.available_amount = self.target_amount.copy()
        # 已成交金额
        self.traded_amount = defaultdict(lambda: 0.)

        # 当前委托
        self.last_order = defaultdict(attrdict)

        # 设置滑点
        self.param_slippages = 1
        # 报单周期间隔(秒)
        self.param_order_period = 10000000

        self.high_limit = {}
        self.low_limit = {}

        self._xx = []

    @timeit
    def get_position_detail_by_code(self, code):
        positions = self.get_positions()
        for p in positions:
            if p['code'] == code:
                return p

    def on_market_trade(self, data):
        """
        响应逐笔成交行情
        """

        # 过滤撤单记录
        if data.nTurnover == 0:
            return

        # print(f'\rtrade: {data.nTime} {data.szCode} {data.nPrice}', end='')
        if data.nPrice < 1:
            self._xx.append(data)

        if data.nTime < 93000000:  # 不参与集合竞价
            return

    def process_pending_order(self, order):

        if order['status'] < 4:

            code = self.as_wind_code(order.code)
            if order['placed_localtime'] - to_timeint(self.now) >\
                self.param_order_period * 1000:

                # 涨跌停价报单，不需要重发
                if (order.price >= self.high_limit[code] or
                    order.price <= self.low_limit[code]):
                    return

            self.logger.warning(f'{code} 撤单')
            self.cancel_order(**order)

    def insert_order(self, code, price):

        if not self.allow_trading:
            return

        # 计算需要委托的报单方向与数量
        left_amount = self.available_amount[code]
        price = price + self.param_slippages * 0.01

        side = 1 if left_amount > 0 else -1

        high_limit, low_limit = self.high_limit[code], self.low_limit[code]

        if price >= high_limit:
            self.logger.warning('{code} 触及涨停板价 {high_limit}')
            price = high_limit

        if price <= low_limit:
            self.logger.warning('{code} 触及跌停板价 {low_limit}')
            price = low_limit

        # 买入卖出数量均以100股为单位
        volume = int(abs(left_amount) / price / 100) * 100

        if volume > 0:
            code_ = code.split('.', 1)[0]
            if side == 1:
                order = self.buy(code_, price, volume)
                self.logger.warning(f'{code} 委托买入 {volume} 股')
            else:
                order = self.sell(code_, price, volume)
                self.logger.warning(f'{code} 委托卖出 {volume} 股')

            self.last_order[code] = order

            amount = price * volume * side
            self.pending_amount[code] += amount
            self.available_amount[code] -= amount
            self.log_order_stats(code)

    def on_market_snapshot(self, data):
        """
        响应快照行情
        """
        # print(f'\r{data.nTime} {data.szCode}. {data.nMatch}', end='')

        code = data.szWindCode

        # 记录涨跌停价格
        if code not in self.high_limit:
            self.high_limit[code] = data.nHighLimited
            self.low_limit[code] = data.nLowLimited

        # 不参与集合竞价
        if data.nTime < 93000000:
            return

        if code in self.target_codes:

            # 处理未成交报单委托
            if code in self.last_order:
                self.process_pending_order(self.last_order[code])

            self.insert_order(code, data.nMatch)

    def on_order(self, order):
        """
        响应报单回报
        """
        code = self.as_wind_code(order.code)
        self.last_order[code] = \
            self._orders[order['order_original_id']]

        # 部分撤单
        if order.status == dtp_type.ORDER_STATUS_PARTIAL_CANCELLED:
            print('部分撤单\n', order)

    def on_trade(self, trade):
        """
        响应成交回报
        """
        code = self.as_wind_code(trade.code)
        self.last_order[code] = \
            self._orders[trade['order_original_id']]
        # 委托占用金额转为成金额
        if trade.fill_status == dtp_type.FILL_STATUS_FILLED:

            _sign = 1 if trade.order_side == dtp_type.ORDER_SIDE_BUY else -1

            traded_amount = trade.fill_amount * _sign
            released_amount = (self.last_order[code]['price'] *
                               trade.fill_quantity * _sign)
            self.traded_amount[code] += traded_amount
            self.pending_amount[code] -= released_amount
            self.log_order_stats(code)

    def on_order_cancelation(self, data):
        """
        响应撤单回报
        """
        print('撤单回报')
        # 撤单后释放委托占用金额
        # cancelled_vol = data.cancelled_quantity
        # self.pending_amount[data.szWindCode] -= price * cancelled_vol
        code = self.as_wind_code(data.code)
        amount = data.freeze_amount * -1
        self.pending_amount[code] -= amount
        self.available_amount[code] += amount

        self.log_order_stats(code)

    def as_wind_code(self, code):
        if code.startswith('6'):
            return code + '.SH'
        return code + '.SZ'

    def log_order_stats(self, code):
        print(
            f'代码: {code}',
            f'目标金额: {self.target_amount[code]}',
            f'已成交: {self.traded_amount[code]}',
            f'报单冻结: {self.pending_amount[code]}',
            f'可用金额: {self.available_amount[code]}',
        )

    def write2sql(self):
        now = datetime.datetime.now().strftime('%Y%m%d %H:%M:%S.%f')
        update_date = now[:8]
        update_time = now[9:]
        session = self._Session()

        # 写入账户资金信息
        capital = self.get_capital()
        capital['update_date'] = update_date
        capital['update_time'] = update_time
        session.add(CapitalModel.from_msg(capital))

        # 写入成交记录
        trades = self.get_trades()
        last_trade = (session
                      .query(TradeModel)
                      .order_by(TradeModel.fill_exchange_id.desc())
                      .first())

        if last_trade is None or last_trade.update_date < update_date:
            today_last_trade_no = -1
        else:
            today_last_trade_no = int(last_trade.fill_exchange_id)
        # 仅追加新的成交记录
        for trade in trades:
            if int(trade.fill_exchange_id) > today_last_trade_no:
                trade['update_date'] = update_date
                trade['update_time'] = update_time
                session.add(TradeModel.from_msg(trade))

        # 刷新持仓记录
        positions = self.get_account_positions()
        (session
            .query(PositionModel)
            .filter(PositionModel.update_date == update_date)
            .delete(synchronize_session=False))
        for position in positions:
            position['update_date'] = update_date
            position['update_time'] = update_time
            session.add(PositionModel.from_msg(position))

        session.commit()
        session.close()