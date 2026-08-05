# Account Cooldown Risk Plan

## 规则

模式名：`ACCOUNT_LOSS_STREAK_COOLDOWN`。作用域为整账户；连续 3 个完整 Trade Cycle 的净交易亏损触发 12 小时冷静期；不能人工提前解除或通过切换策略绕过。

`net_trade_pnl = realized_pnl - trading_fees - funding_fees - other_actual_execution_costs`。只有从 Flat 进入持仓并回到 Flat 的完整 Trade Cycle 才计一次；部分成交/部分平仓/多次 Fill 不重复计数。Long/Short 各自有稳定 cycle identity，账户按 `fully_closed_at, stable_trade_cycle_id` 排序。

## 冷静期行为

进入后禁止 OPEN、INCREASE、补仓、摊平、金字塔和人工新增风险；取消未成交增加风险订单。允许 REDUCE、CLOSE、EMERGENCY_CLOSE、PROTECTION、止损止盈和降低保证金风险的取消。持久化 started/ends、streak、reason，跨进程/重启有效；到期后只有 Reconciliation、Health、Market Data 正常才恢复开仓。

## 测试

覆盖三连亏、盈利/保本归零、部分平仓只计一次、重启、并发、精确12小时、pending cancel、减仓允许和重复 Fill 不重复计数。
