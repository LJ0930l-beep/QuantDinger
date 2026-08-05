# Release Readiness Checklist

## Safety Core

- [ ] SC-13 全部入口通过 Admission；受限来源 DISABLED/退役，baseline 只下降。
- [ ] SC-14 Consumer、Projection、Shadow、Reconciliation、Health、G4-B 完成并可重建。
- [ ] SC-15 legacy 真相/旁路退役，完成 restart/rollback/replay/network/DB/orphan 演练。
- [ ] Architecture Guard ≤46；Entry-Point baseline ≤31；AI Boundary baseline 不增加。
- [ ] Live OFF，未连接真实账户，未请求真实凭证。

## Product Gates

- [ ] MAD-01 和 Gate capability matrix 覆盖账户、地区、权限、Spot/Perpetual/Delivery/Options/Stocks/ETFs、TestNet、持仓/保证金、市场数据和订单类型。
- [ ] DATA-01 覆盖 point-in-time、序列、去重、缺失、冲突、快照和 rebuild。
- [ ] BT-01 覆盖 next-open、费用、滑点、Funding、部分成交、清算、期限、Walk-forward 和可重放。
- [ ] PS-01 复用同一策略版本、参数、杠杆、保证金、仓位、Hard Risk、Admission。
- [ ] STRAT/SMC 只输出 Candidate Trade Plan，拥有数据/规则/参数版本和拒绝原因。
- [ ] PORT-01 用户明确选择杠杆与保证金模式；无静默降级或自动改写。
- [ ] Account cooldown 具备跨重启、精确 12 小时、三次完整 Trade Cycle、减仓允许测试。

## Frontend / Operations

- [ ] Dashboard 只读且显示 stale/unavailable/unauthorized。
- [ ] 观测覆盖 Admission、Outbox、Projection、Reconciliation、Health、延迟与告警。
- [ ] 所有任务有 unit/PostgreSQL/integration/replay/concurrency/failure/security/guard 证据。

最终状态只能是 `CONTROLLED LIVE READY / LIVE OFF`，本清单不是 Live 启用授权。
