# Gate First 确定性多资产量化系统：总控执行计划

## 0. 项目定位

本项目是一个确定性、可审计、可回测、可重放的多资产量化交易系统。所有可执行交易事实必须来自版本化市场数据、确定性规则、Decimal 数值、用户明确配置、权威账户/持仓事实、Hard Risk、Reservation 与 Admission。

AI、LLM、Agent 不拥有交易决策权；当前产品契约将其交易 authority 固定为 **0%**。Live 永久保持 OFF，系统最高只能达到 `CONTROLLED LIVE READY / LIVE OFF`。

## 1. 当前基线

| 项目 | 当前事实 |
| --- | --- |
| 后端仓库 | `LJ0930l-beep/QuantDinger` |
| 当前 main | `202c6f6cfc077380fb9b26ebed8cfcd75ec5ab2e` |
| 已合并安全核心 | PR #32 Runtime Entry Facts；PR #33 受限 Agent/MCP 入口退役 |
| Safety Core | 13 / 16；PR #33 合并后仍需完成完整 SC-13 收口 |
| Entry-Point legacy baseline | 31；只能下降，不能回升 |
| Architecture Guard | 46；只能下降，不能回升 |
| AI Boundary | 已登记遗留导入只能减少；不得新增 provider 或交易决策流 |
| Live | OFF；不请求真实 API Key，不连接真实交易所 |
| docs/codex | 不属于本路线，保持未跟踪且不修改 |

## 2. Gate First 顺序

每个任务先有明确 Gate、Definition of Done、停止条件和证据，再进入代码。未登记在 `COMPLETE_EXECUTION_BACKLOG.md` 的任务不得自动启动。

1. **SC-13 Entry-Point Convergence**：REST、MANUAL、STRATEGY、PROTECTION 经 Canonical Entry → Hard Risk → Admission；Agent/MCP/Grid 保持 DISABLED 或退役。
2. **SC-14 Read Cutover / G4-B**：Admission Outbox → 注册 Consumer → Candidate Projection → Shadow Diff → Reconciliation → Derived Health → 只读 API。
3. **SC-15 Legacy Retirement**：移除旧交易真相与旁路，完成重启、回滚、重复事件、数据库/网络故障和孤儿事实演练；达到 Safety Core 16/16，但仍不启用 Live。
4. **MAD-01 Multi-Asset Domain** 与 Gate capability matrix：先锁定产品类型、账户模式、权限、规则和 venue 能力，不能用猜测填补能力。
5. **DATA-01 → BT-01 → PS-01**：市场数据、回测、Paper/Shadow 必须同一套确定性语义。
6. **STRAT-01 → STRAT-RESEARCH → Strategy Library → SMC-01**：先审计旧策略，再内建确定性策略；策略只输出 Candidate Trade Plan。
7. **PORT-01 → RISK-COOLDOWN → FE-01 → OPS-01**：仓位/保证金、连续亏损冷静期、前端只读产品、运维观测。
8. **LIVE-R01**：只准备 Controlled Live Ready 证据，Live 仍 OFF。Binance/OKX 适配器只能在 Gate 垂直闭环后独立审批。

## 3. 共同不可协商边界

- 任何入口必须经过 Canonical Entry → Hard Risk → Admission → Outbox → Controlled Executor。
- 任何新增交易旁路、直接 Executor/Exchange 调用、随机 UUID 业务身份、float 权威数值、未来数据、未版本化参数或 correlation 进入经济身份，立即停止。
- 需要真实凭证、真实交易所写入、Live、LLM/AI 影响交易决策、破坏 migration、提高 Guard baseline 或修改 `docs/codex/` 时立即停止。
- 每个实现 PR 独立分支、独立 Draft、精确 Head CI、Diff 自审、Gate 通过后才合并；同一时间最多两条实现线。

## 4. 进度口径

只有已合并并满足 Definition of Done 的子任务进入 Official Progress；Draft 或未合并 Head 只能进入 Candidate Progress。每项都必须记录证据链接、测试、PostgreSQL（若适用）、回放/并发/故障测试、Guard、Live 状态和停止条件。

## 5. AI 边界

AI/LLM/Agent 可作为离线研究或界面说明的非权威输入，但不得决定 action、方向、数量、杠杆、保证金成本、止损止盈、策略切换、风险预算或 Admission。Agent、MCP、Grid 默认 DISABLED；无 LIVE 枚举、无隐式启用和无真实交易调用。
