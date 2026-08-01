# Frontend Trading Workspace Plan

产品名称：Quant Trading Dashboard。页面分区：Account Overview、Gate 连接/权限、Balance/Equity/Margin、Positions、Orders、Fills、Strategy Library/Configuration、Leverage/Margin Configuration、Backtest、Paper、Shadow、Risk、Cooldown、Reconciliation、Health、Alerts、Performance/Attribution、K-Line Workspace。

K 线只读展示 Candles、Volume、指标、SMC 结构、信号、Stop/Target、Pending Order、Actual Fill、平均持仓和清算估计；点击信号显示策略/参数/数据时间、通过/拒绝原因、杠杆/保证金、数量计算、Hard Risk、Admission、Order、Fill、Reconciliation 事实。

前端不得直接调用 Gate、Executor、Exchange 或推断权威状态；所有写操作若未来批准，仍须经过 Canonical Entry → Hard Risk → Admission。Live、stale、unavailable、unauthorized 必须来自服务端事实。
