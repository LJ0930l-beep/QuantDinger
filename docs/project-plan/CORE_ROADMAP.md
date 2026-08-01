# Safety Core 路线图

## 共同基线

- 当前 main：`202c6f6cfc077380fb9b26ebed8cfcd75ec5ab2e`。
- Safety Core：13/16；PR #33 已合并，但 SC-13 只有在所有入口收口证据完成后才算 DONE。
- Architecture Guard：46；Entry-Point legacy baseline：31；两者只允许下降。
- Live OFF；不连接真实账户；不扩大现有 Schema 之外的范围，除非该阶段明确批准。

## SC-13 Entry-Point Convergence（当前主线）

| Task | 状态 | Definition of Done |
| --- | --- | --- |
| C13-01 Inventory | IN_PROGRESS | REST、MANUAL、STRATEGY、PROTECTION、AGENT、MCP、GRID 与所有 legacy bypass 有路径/符号/调用证据 |
| C13-02 Adapter Contract | CANDIDATE | 每类入口的 action/actor/scope/idempotency/correlation/fingerprint 无损映射，未知事实 fail closed |
| C13-03 REST/MANUAL | NOT_STARTED | 仅经 Admission，caller-owned 原子链与回归证据完整 |
| C13-04 STRATEGY | NOT_STARTED | Strategy 只输出 Candidate Trade Plan，经 Entry/Hard Risk/Admission |
| C13-05 PROTECTION | NOT_STARTED | 只允许 REDUCE_RISK，禁止 OPEN/INCREASE/CANCEL |
| C13-06 AGENT/MCP/GRID | PARTIAL | Agent/MCP quick-trade 已退役（PR #33）；Grid 仍需独立核验，默认 DISABLED |
| C13-07 Legacy records | PARTIAL | PR #33 已将 baseline 降至31；只有已证明不可达的记录才可继续删除 |

停止条件：出现未知入口、需要伪造 scope/position/account fact、绕过 Hard Risk、产生 legacy order graph、直接 Executor/Exchange 调用或 baseline 上升。

## SC-14 Read Cutover / G4-B（DEFERRED）

依赖 SC-13 DONE。顺序固定为：Consumer contract → event registry → lossless Projection mapping → caller-owned idempotent Consumer → Candidate Generation → Shadow Diff → Reconciliation → Derived Health → G4-B → read-only API。Projection、Shadow、Health 永不产生交易决策。

## SC-15 Legacy Retirement（NOT STARTED）

仅在 SC-13 与 SC-14 DONE 后：inventory → 移除 legacy CommandGraph/OrderIntent 权威角色 → 删除 direct Executor/Exchange/quick-trade bypass → read cutover → failure drills → orphan/identity audit → Safety Core 16/16。不得删除未经证明的事实或启用 Live。
