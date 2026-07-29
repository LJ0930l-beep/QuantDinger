# 发布就绪检查表

本表定义四个不同层级。勾选必须附精确 Head、PR、日期和可复核证据；没有证据则保持未勾选。

## 1. Safety Core Complete

- [ ] PR-13 入口收口完成，REST / MANUAL / STRATEGY / PROTECTION / AGENT / MCP / GRID 全部通过 Admission。
- [ ] Entry-Point legacy baseline 仅减少并达到批准目标；Architecture Guard 未增加。
- [ ] PR-14 G4-B 完成：Admission Outbox → Registered Projection Consumer → Candidate Projection → Shadow Diff → Reconciliation → Derived Health。
- [ ] Projection 可从事实重建，generation / watermark / replay / conflict 合同通过。
- [ ] PR-15 清除遗留权威读写和已迁移旁路；无孤儿 Reservation、Outbox 或未知身份。
- [ ] 升级、重启、回滚、重复事件、网络故障、数据库故障演练通过。
- [ ] 16 / 16 完成记录存在；Live 仍为 OFF。

## 2. Paper / Shadow Ready

- [ ] 真实只读市场输入已定义新鲜度、缺失和异常语义。
- [ ] Paper Account、Fill、Fee、Slippage、Position 与 PnL 可重放。
- [ ] Shadow Strategy Run 与策略计划/市场结果对比可解释。
- [ ] 长期运行、重启恢复、监控和告警通过批准观察周期。
- [ ] 无身份漂移、无未解释对账异常。

## 3. Controlled Live Ready

- [ ] 所有入口都经过 Admission；Hard Risk fail-closed。
- [ ] Reservation 无泄漏；Outbox 无积压；Projection 可重建；Shadow Diff 可解释。
- [ ] Reconciliation 健康、Submission Unknown 恢复、Kill Switch 演练通过。
- [ ] 数据库重启、网络超时、重复事件、时钟漂移检查通过。
- [ ] 凭证隔离、备份恢复、监控告警、审计日志和人工紧急停止均通过。
- [ ] 单交易所、单账户、单策略、最小额度、独立凭证和人工审批已明确。
- [ ] Live 默认 OFF，并有单独正式启用决定；本清单本身不构成启用授权。

## 4. Product Release Ready

- [ ] Paper / Shadow Runtime、Strategy Platform、SMC/ICT 规则和 AI Analysis Layer 分别通过产品闸门。
- [ ] Dashboard API 合同、鉴权、陈旧/不可用状态和事件流语义可验证。
- [ ] 用户体验不将任何按钮点击表示为真实交易成功。
- [ ] 运营、支持、告警、审计、风险解释和复盘流程具备明确责任人。

## 证据记录模板

| 日期 | 层级 | 项目 | 状态 | Head | PR | 验证证据 | 审批结论 |
|---|---|---|---|---|---|---|---|
| TBD | TBD | TBD | NOT_STARTED | TBD | TBD | TBD | TBD |
