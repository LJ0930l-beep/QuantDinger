# Market Data and Backtest Plan

## DATA-01

覆盖 Trades、Candles、Order Book、Ticker、Mark/Index、Funding、Instrument Rules、Risk Tiers、Expiry、Options Chain/Mark/Underlying、股票时段和公司行动。每条事实带 source、observed/occurred time、sequence、dataset snapshot、规则版本和 Decimal 数值。

缺失、陈旧、乱序、冲突、重复、断线必须显式记录并 fail closed；Point-in-Time 查询不能看见未来，禁止隐式 forward fill。数据集必须可重建。

## BT-01

回测使用 deterministic clock、next-open 成交语义，明确 Market/Limit、未成交/部分成交、spread、slippage、maker/taker、fees、funding、multiplier、margin、leverage、liquidation、delivery/rollover、option expiry、stock sessions、corporate actions、walk-forward、OOS、benchmark 和 reproducibility。

禁止同收盘成交、未来 K 线泄漏、未版本化参数/手续费、使用当前规则回放历史或不可解释的 forward fill。回测、Paper、Shadow、未来受控执行必须共享策略/参数/仓位/Hard Risk/Admission 语义。
