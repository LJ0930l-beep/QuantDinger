# Gate Venue Integration Plan

## 范围

第一垂直目标是 Gate；Binance、OKX 仅保留 Venue-Neutral 公共合同，在 Gate 垂直闭环前不实现业务适配。

第一阶段产品类型：Crypto Spot、Crypto Perpetual、Crypto Delivery Futures、Crypto Options、Gate Stocks、Gate ETFs。每类独立定义 Instrument、Account、Position、Order、Fill、Fee、Margin、Market Data、Reconciliation、Backtest、Paper 合同。

## Capability Matrix

必须逐项验证：账户模式、地区、API 权限、产品类型、TestNet/Demo、one-way/hedge、cross/isolated、杠杆读取/设置、行情、订单类型、Fill ID、Rate Limit。未知能力为 unsupported/fail closed，不继承其他 market profile。

## Gate 顺序

`GATE-00 capability → GATE-01 auth/permission → GATE-02 balances/equity → GATE-03 instruments → GATE-04 spot → GATE-05 perpetual → GATE-06 leverage sync → GATE-07 margin sizing → GATE-08 delivery → GATE-09 stocks/ETFs → GATE-10 options`。

每个 Gate 必须包含 read-only evidence、scope/fingerprint、Decimal、replay、concurrency、failure、security 和 CI 证据；不得连接真实写接口。
