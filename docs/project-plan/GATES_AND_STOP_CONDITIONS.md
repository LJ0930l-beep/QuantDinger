# 闸门与停止条件

## 自动合并前提

每个 PR 必须证明精确 Head 的 Backend CI、Security CI、适用的 PostgreSQL 测试、`git diff --check`、编译/构建、Architecture Guard、Entry-Point Guard 和 AI Boundary Guard 均通过；Guard baseline 不增加；Live OFF；无 Exchange/Executor 旁路；无随机业务身份；无未版本化数据/参数；且累计 diff 自审通过。

## 立即停止条件

出现以下任一项即停止并报告：

1. 需要接入或依赖 LLM / AI 模型，或模型输出影响交易决定、Canonical Entry、Risk Facts、Position Sizing、策略参数或风险预算。
2. 需要在线学习、强化学习、真实 API Key、Live、真实交易所写操作，或跳过 Admission / Hard Risk。
3. 需要伪造账户、仓位、行情、策略事实，使用破坏性 migration，或用随机 UUID/当前时间作为业务身份。
4. correlation_id 进入 Economic Fingerprint、任一 Guard baseline 增加、数据可能未来泄漏、策略不可重放，或连续两次无法修复 P0。
5. 需要修改 `docs/codex/`，或 Controlled Live Ready 已完成。

## AI Boundary Guard

`backend_api_python/architecture/ai_boundary_guard.py` 静态检查确定性交易核心与策略服务。它禁止新的 AI/LLM provider import，也禁止直接将模型输出送入 Canonical Entry、Risk Facts 或 Position Sizing。`ai_boundary_manifest.json` 的逐项遗留基线只允许减少；该 Guard 是静态边界，不替代遗留代码的可达性审计。

## 读模型边界

Projection、Shadow Diff、Reconciliation 与 Derived Health 只能解释、比较或暴露事实；它们永不得产生交易决定。G4-B 留待 SC-14。
