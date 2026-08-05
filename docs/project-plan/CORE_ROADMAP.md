# Safety Core 路线图

## 共同基线

- 当前 main：`202c6f6cfc077380fb9b26ebed8cfcd75ec5ab2e`。
- Safety Core：13/16；PR #33 已合并，但 SC-13 只有在所有入口收口证据完成后才算 DONE。
- Architecture Guard：46；Entry-Point legacy baseline：31；两者只允许下降。
- Live 当前 OFF；只有 Gate TestNet、Paper/Shadow、Canary、恢复/对账和人工二次确认全部有证据后才可单独批准启用。开发目标是完整可用产品，不以某个阶段或单个 PR 作为完成标志。

## SC-13 Entry-Point Convergence（当前主线）

> 本地证据基线（2026-08-05 更新）：SC-13 契约测试 78 passed / 20 subtests（含 canonical entry V2、entry admission V2 adapters、entry convergence gate、runtime entry admission service/http、protection entry、strategy V2 candidate）；Entry-Point legacy baseline 31，`test_entrypoint_convergence_guard.py` + `test_sc15_terminal_guard_proof.py` 13 passed。

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

## SC-14 Read Cutover / G4-B（DEFERRED）

依赖 SC-13 DONE。顺序固定为：Consumer contract → event registry → lossless Projection mapping → caller-owned idempotent Consumer → Candidate Generation → Shadow Diff → Reconciliation → Derived Health → G4-B → read-only API。Projection、Shadow、Health 永不产生交易决策。

**本地证据基线（2026-08-05）**：契约层已全部落地并测试通过——`test_outbox_projection_repository_postgres.py`（caller-owned、幂等 Consumer）、`test_reconciliation_repository_postgres.py`（HEALTHY/DEGRADED/UNHEALTHY）、`test_shadow_diff_repository_postgres.py`（Decimal tolerance）、`test_g4b_readonly_contracts.py`、`test_readonly_cutover_contracts.py`、`test_projection_consumer_contracts.py`；15 个 `/api/quant/*/readonly` 端点实测返回显式 UNAVAILABLE/READY，不静默伪装。**未执行 Read Cutover**（G4-B 未批准，不切换权威读模型）。

## SC-15 Legacy Retirement（NOT STARTED）

仅在 SC-13 与 SC-14 DONE 后：inventory → 移除 legacy CommandGraph/OrderIntent 权威角色 → 删除 direct Executor/Exchange/quick-trade bypass → read cutover → failure drills → orphan/identity audit → Safety Core 16/16。不得删除未经证明的事实或启用 Live。
