# Multi-Asset Domain Plan

## 统一但不混淆

Instrument、Account、Position、Order、Fill、Fee、Margin、MarketData、Reconciliation、BacktestProfile、PaperProfile 都必须有稳定版本和 scope。Spot、Perpetual、Delivery、Options、Stocks/ETFs 的原生语义不能互相猜测或混用。

## 关键差异

- Spot：数量和余额，不假设杠杆、Funding 或强平。
- Perpetual：合约乘数、标记价、Funding、保证金、杠杆、清算和持仓模式。
- Delivery：到期、交割、展期/结算规则。
- Options：期权链、到期、Mark/Underlying、IV/Greeks（仅有权威来源时）。
- Stocks/ETFs：交易时段、公司行动、股票余额与市场规则。

## 不变量

所有经济金额/价格/数量使用 Decimal；scope 包含 venue、market、account、instrument；规则快照版本化；Fill ID 稳定；未知能力 fail closed；同一账户不能将不同产品的保证金、持仓或费用直接聚合。
