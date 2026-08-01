# Gate 与停止条件

## 合并前 Gate

每个精确 Head 必须满足：Backend CI、Security CI、适用的 PostgreSQL/集成测试、compile/build、`git diff --check`、Architecture Guard、Entry-Point Guard、AI Boundary Guard 全部通过；Draft PR 的 Diff 与批准范围一致；Live OFF；工作区与 `docs/codex/` 安全边界清晰。

## 立即停止

出现以下任一项立即暂停该任务并报告：

1. 需要真实 API Key、账户、交易所写操作或 Live。
2. AI/LLM/Agent 影响交易决策、Risk Facts、Position Sizing 或策略参数。
3. 需要伪造 account/position/market facts，或用随机 UUID/当前时间作业务身份。
4. correlation_id 进入 economic fingerprint；未来数据、float 权威数值或未版本化参数进入链路。
5. Architecture/Entry-Point/AI baseline 增加。
6. 需要修改 `docs/codex/`、破坏 migration，或出现 raw DB exception、无法重放、死锁、孤儿事实。
7. 连续两次无法修复 P0；或目标 Gate 缺少权威证据。

## 只读模型边界

Projection、Shadow Diff、Reconciliation、Derived Health 只能解释和暴露事实，不能产生交易决策、Reservation、下单或覆盖 Admission。G4-B 之前不得切换权威读模型。

## Gate 证据格式

每项任务记录：范围、基线/Head、提交、改动文件、单测、PostgreSQL、集成、重放、并发、故障、安全、Guard、Live、停止条件和回滚方案。
