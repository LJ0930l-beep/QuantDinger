# Strategy Platform and Library Plan

## 审计优先

在复用旧 Strategy、Signal、Indicator、Grid、Pending Order、Scheduler、Worker、Backtest、Protection 前，必须登记路径、逻辑、确定性、可重放性、未来数据风险、Admission 旁路、保留/重写/退役结论。未经审计不得直接依赖。

## 内建策略

第一批：Buy and Hold/Flat Benchmark、EMA+ADX Trend、Donchian+ATR Breakout、Bollinger+RSI Mean Reversion、Dual Thrust Intraday、确定性 SMC/ICT。Basis、Funding、Delivery Convergence 后置。

每个策略必须有 Definition、Version、Parameter Schema/Snapshot、Data Dependency Snapshot、Signal Fact、Entry/Exit/Invalidation/Target、Backtest、Walk-forward、OOS、Paper、Shadow、Attribution。

策略只能输出 Candidate Trade Plan；方向、数量、杠杆、保证金、风险预算、停止损益和执行权限由用户配置、Position Sizing、Hard Risk、Admission 决定。
