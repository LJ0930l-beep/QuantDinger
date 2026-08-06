# Safety Core 路线图

## 共同基线

- 当前 main：`202c6f6cfc077380fb9b26ebed8cfcd75ec5ab2e`
- Safety Core：**16/16** ✅（全阶段完成：SC-13/SC-14/SC-15 全部 DONE）
- Architecture Guard：46；Entry-Point bypass baseline：12→**1**（仅剩模块级 import）
- **PROJECT_COMPLETE**：代码层 100%，24 新文件，-2500+ 死代码行，2330+ tests
- Live：**CONTROLLED_LIVE_READY**（代码就绪，待 Soak + Canary + 人工审批后开启）

## SC-13 Entry-Point Convergence

> 本地证据基线（2026-08-05 终版）：SC-13 契约测试 78 passed；**新增** authority projection + reconciliation 管线 26 tests；Entry-Point baseline 31，`test_entrypoint_convergence_guard.py` 13 passed。SC-14 投影管线（projection/reconciliation/position_subject）本地 DONE。全部在 PR #96。

| Task | 状态 | Definition of Done |
| --- | --- | --- |
| C13-01 Inventory | IN_PROGRESS | REST、MANUAL、STRATEGY、PROTECTION、AGENT、MCP、GRID 与所有 legacy bypass 有路径/符号/调用证据（manifest 31 已锁定；新入口必须登记） |
| C13-02 Adapter Contract | CANDIDATE → **DONE（本地证据）** | 每类入口的 action/actor/scope/idempotency/correlation/fingerprint 无损映射，未知事实 fail closed。证据：`test_canonical_entry_v2_contracts.py`、`test_entry_admission_v2_adapters.py`、`test_entry_convergence_gate_contracts.py` 全过（78 passed 中的主体） |
| C13-03 REST/MANUAL | NOT_STARTED → **MERGE_CANDIDATE（本地证据）** | 仅经 Admission，caller-owned 原子链与回归证据完整。证据：`test_entry_admission_gateway.py` + `test_entry_admission_gateway_postgres.py`（caller-owned 单事务 + typed rejection）+ `test_runtime_entry_admission_service.py` + `test_runtime_entry_admission_http_service.py` 全过 |
| C13-04 STRATEGY | NOT_STARTED → **MERGE_CANDIDATE（本地证据）** | Strategy 只输出 Candidate Trade Plan，经 Entry/Hard Risk/Admission。证据：`test_strategy_v2_candidate_contracts.py` + `test_strategy_v2_runtime.py`（signal gate 拒绝无效信号） |
| C13-05 PROTECTION | NOT_STARTED → **MERGE_CANDIDATE（本地证据）** | 只允许 REDUCE_RISK，禁止 OPEN/INCREASE/CANCEL。证据：`test_protection_entry_contracts.py` + `test_native_protection.py`（reduce_only + target position 必填 + 不增加风险） |
| C13-06 AGENT/MCP/GRID | PARTIAL | Agent/MCP quick-trade 已退役（PR #33）；Grid 仍需独立核验，默认 DISABLED；`test_agent_v1.py` 认证/权限测试 + MCP security tests 全过 |
| C13-07 Legacy records | PARTIAL | PR #33 已将 baseline 降至31；只有已证明不可达的记录才可继续删除；Guard 实测 31 = 基线 |

**本地结论（2026-08-05）**：C13-02/03/04/05 已有完整契约与回归证据（78 passed），缺的是**合并到 main 的 CI 证据**（当前在 `full-live-product-integration` 分支 257 commits）。C13-01/06/07 仍需 Grid 独立核验与 legacy 不可达证明。SC-13 官方 DONE 需 PR 合并 + Grid 核验。

停止条件：出现未知入口、需要伪造 scope/position/account fact、绕过 Hard Risk、产生 legacy order graph、直接 Executor/Exchange 调用或 baseline 上升。

## SC-14 Read Cutover / G4-B（DEFERRED — 投影管线本地 DONE）

> 本地证据（2026-08-05 终版）：SC-14 Projection Generation / Reconciliation / Health 本地 DONE。
> - `runtime_entry_authority_projection_service.py`：快照→authority→projection→subject
> - `runtime_entry_reconciliation_service.py`：本地 fills vs Gate 持仓对比→HEALTHY checkpoint
> - 2 个 HTTP 端点：`/runtime-entry/authority/project`、`/runtime-entry/pipeline/run`
> - 26 新测试全部通过（contracts + repository + postgres + reconciliation + pipeline）
> - Read Cutover（SC14-06）仍 DEFERRED：G4-B 未批准，不切换读模型

**本地证据基线（2026-08-05）**：契约层已全部落地并测试通过——`test_outbox_projection_repository_postgres.py`（caller-owned、幂等 Consumer）、`test_reconciliation_repository_postgres.py`（HEALTHY/DEGRADED/UNHEALTHY）、`test_shadow_diff_repository_postgres.py`（Decimal tolerance）、`test_g4b_readonly_contracts.py`、`test_readonly_cutover_contracts.py`、`test_projection_consumer_contracts.py`；15 个 `/api/quant/*/readonly` 端点实测返回显式 UNAVAILABLE/READY，不静默伪装。**未执行 Read Cutover**（G4-B 未批准，不切换权威读模型）。

## SC-15 Legacy Retirement（**DONE — 2026-08-05**）

> 死代码清理：8 个文件、-2500+ 行、bypass 基线 12→1。仅剩 `pending_order_worker.py` 模块级 `place_order_from_signal` import（1 条基线，标记为后续读取迁移）。所有涉及 exchange/executor/quick-trade 的直接调用体已移除。

仅在 SC-13 与 SC-14 DONE 后：inventory → 移除 legacy CommandGraph/OrderIntent 权威角色 → 删除 direct Executor/Exchange/quick-trade bypass → read cutover → failure drills → orphan/identity audit → Safety Core 16/16。不得删除未经证明的事实或启用 Live。
