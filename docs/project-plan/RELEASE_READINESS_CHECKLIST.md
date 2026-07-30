# 发布就绪检查表

## Safety Core Complete（16 / 16，仍为 Live OFF）

- [ ] SC-13：REST、MANUAL、STRATEGY、PROTECTION 已全部经 Admission；AGENT、MCP、GRID 已禁用或退役。
- [ ] SC-14：Admission Outbox → Consumer → Candidate Projection → Shadow → Reconciliation → Derived Health → 只读 API 完成，且可重建。
- [ ] SC-15：旧交易真相/旁路退役，重启、回滚、重复、网络、数据库、孤儿事实与未知身份演练通过。
- [ ] Architecture / Entry-Point / AI Boundary baseline 均未增加，Live 仍为 OFF。

## 产品就绪链

- [ ] DATA-01、BT-01、PS-01、STRAT-01、SMC-01、PORT-01、FE-01 均有独立 Gate 证据。
- [ ] Paper / Shadow 复用相同的确定性策略、Hard Risk 和 Admission 语义。
- [ ] Dashboard 为鉴权只读界面，明确显示 stale / unavailable / unauthorized。

## Controlled Live Ready（不是 Live 启用）

- [ ] 单交易所、单账户、单确定性策略、隔离凭证和最小额度已审计。
- [ ] Kill Switch、Hard Risk、Admission、Submission Unknown、对账、紧急平仓、数据库/网络恢复、监控、告警、人审与故障演练均通过。
- [ ] 明确记录 `CONTROLLED LIVE READY / LIVE OFF`；本清单不构成任何 Live 启用授权。
