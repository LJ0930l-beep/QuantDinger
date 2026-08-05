# Gate First 确定性多资产量化系统：总控执行计划

## 0. 项目定位

本项目是一个确定性、可审计、可回测、可重放的多资产量化交易系统。所有可执行交易事实必须来自版本化市场数据、确定性规则、Decimal 数值、用户明确配置、权威账户/持仓事实、Hard Risk、Reservation 与 Admission。

AI、LLM、Agent 不拥有交易决策权；当前产品契约将其交易 authority 固定为 **0%**。Live 不是永久禁用，而是必须经过独立的人工确认、Canary 证据和账户级 Kill Switch 闸门；在这些闸门完成前保持 OFF。

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
| Live | 当前 OFF；必须通过 TestNet、Paper/Shadow、Canary 和人工确认闸门后才可单独启用 |
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
8. **LIVE-R01**：在 Gate Spot/Perpetual TestNet、Paper/Shadow、恢复/对账和 Canary 证据完成后，准备受控 Live；Live 开关仍默认 OFF，且不由研究或 AI 自动打开。

## 6. V8 完整产品交付目标

本计划的阶段只是内部施工顺序，不是最终交付物。最终产品必须形成一条可操作的闭环：

`Market Data → Strategy Runtime → Candidate Trade Plan → Position Sizing → Canonical Entry V2 → Hard Risk → Reservation → Admission → Durable Outbox → Trading Worker → Gate Executor → Order/Fill → Ledger/Position → Reconciliation → Read API → Frontend`。

第一批正式产品为 Gate Spot 和 Gate Perpetual；Delivery、Options、Stock/ETF 只有在第一批产品稳定后再接入。Backtest、Paper、TestNet、Canary 和 Live 必须共享同一套 Entry、Risk、Admission、Idempotency、Outbox、Order、Fill、Ledger 与 Reconciliation 语义。

正式环境集合为 `DISABLED / PAPER / SHADOW / TESTNET / CANARY / LIVE`。任何环境转换都必须有版本化证据、人工可见状态和可回滚路径；Live 不得从环境变量、Agent、LLM 或策略代码隐式启用。

最终验收至少覆盖：凭证加密保存与权限检测、真实只读账户读取、确定性策略与回测、Paper/TestNet 生命周期、杠杆/保证金/费用/Funding、重启恢复、重复请求幂等、对账差异、Kill Switch、前端全操作流、Migration/Backend/Frontend/OpenAPI/Security/E2E 测试，以及 Gate Spot/Perpetual 的小额 Canary 证据。当前集成分支已增加 durable PAPER order/fill/event/checkpoint facts，但仍处于集成开发中；不得把任一单独合同或 Draft PR 宣称为项目完成。

## 3. 共同不可协商边界

- 任何入口必须经过 Canonical Entry → Hard Risk → Admission → Outbox → Controlled Executor。
- 任何新增交易旁路、直接 Executor/Exchange 调用、随机 UUID 业务身份、float 权威数值、未来数据、未版本化参数或 correlation 进入经济身份，立即停止。
- 需要真实凭证、真实交易所写入、Live、LLM/AI 影响交易决策、破坏 migration、提高 Guard baseline 或修改 `docs/codex/` 时立即停止。
- 每个实现 PR 独立分支、独立 Draft、精确 Head CI、Diff 自审、Gate 通过后才合并；同一时间最多两条实现线。

## 4. 进度口径

只有已合并并满足 Definition of Done 的子任务进入 Official Progress；Draft 或未合并 Head 只能进入 Candidate Progress。每项都必须记录证据链接、测试、PostgreSQL（若适用）、回放/并发/故障测试、Guard、Live 状态和停止条件。

## 5. AI 边界

AI/LLM/Agent 可作为离线研究或界面说明的非权威输入，但不得决定 action、方向、数量、杠杆、保证金成本、止损止盈、策略切换、风险预算或 Admission。Agent、MCP、Grid 默认 DISABLED；无 LIVE 枚举、无隐式启用和无真实交易调用。
