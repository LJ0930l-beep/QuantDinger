# QuantDinger 最终产品完成计划

> 文档状态：**SINGLE COMPLETION TARGET / AUTHORITATIVE DELIVERY PLAN**
>
> 制定日期：2026-08-03
>
> 产品范围：Gate Spot + Gate Perpetual 的确定性量化交易系统
>
> 最终目标：完成开发、测试、部署、恢复、对账、Canary 与受控 Live 能力，不再把单个阶段、PR、页面或一次交易所调用称为“项目完成”。

## 1. 唯一完成定义

本项目只有在本文件第 4 节全部工作包和第 9 节最终验收清单全部通过后，才可以标记为 `PROJECT_COMPLETE`。

完成后的系统必须形成并实际运行以下闭环：

```text
Gate Market/Account Facts
→ Deterministic Strategy Runtime
→ Candidate Trade Plan
→ Position Sizing
→ Canonical Entry V2
→ Durable Entry
→ Hard Risk
→ Risk Reservation
→ Admission
→ Transactional Outbox
→ Durable Trading Worker
→ Gate Spot/Perpetual Executor
→ Exchange Order / Fill
→ Immutable Ledger
→ Position / PnL Projection
→ Reconciliation / Derived Health
→ Read API
→ Frontend Trading Workspace
→ Monitoring / Recovery / Audit
```

“完成”必须同时满足：

1. Gate Spot 与 Perpetual 均有完整的读取、下单、撤单、成交、手续费、Funding、仓位和对账链路。
2. Market、Limit、Stop Market、Stop Limit、Cancel、Reduce、Close、Close All 和 Protection 都有明确合同和测试证据。
3. 所有交易入口统一经过 Canonical Entry、Hard Risk、Reservation、Admission 和 Outbox；不存在可达旁路。
4. 相同请求可安全重放；未知提交、重复消息、late fill、重启和网络/数据库故障不会产生重复经济订单。
5. Fill、Ledger、Position、PnL 与 Reconciliation 可重建、可审计且使用 Decimal。
6. 回测、Paper、Shadow、TestNet、Canary、Live 共享同一套策略、风控、身份和订单语义。
7. 前端能够完成账户连接、研究、回测、策略、风险、订单、仓位、对账和运维观察，不依赖 Mock 才能正常工作。
8. 生产部署、迁移、监控、告警、备份、恢复和回滚全部通过。
9. Paper/Shadow、TestNet、故障注入与 Canary 证据完成，无未解释的订单、仓位或资金差异。
10. 系统具备受控 Live 能力；是否投入真实资金是最终人工运营决策，不再属于软件开发缺口。

## 2. 当前基线与进度口径

### 2.1 当前事实

| 项目 | 当前事实 |
| --- | --- |
| 正式后端 main | `5cf9134c020ee8511d37672bc78a83cf82a1d85f` |
| 本地集成 Head | `535deb2b46e00b200cb4f8a2f7a7b9a3804fc78e` |
| 本地相对 main | 201 commits / 212 files |
| 后端 Draft | PR #95，非实盘研究与只读产品链，尚未合并 |
| 前端本地 Head | `7eeb55a1270b93c8d6ab952f767bb01c1eef7c96` |
| 前端 Draft | PR #1，尚未合并，另有 8 个本地提交待推送 |
| Safety Core | 13 / 16 |
| 当前 Order Guard | 37 个已登记遗留调用 |
| 当前 Entry Guard | 12 个已登记遗留入口 |
| Live | OFF |
| `docs/codex/` | 未跟踪、不得修改或提交 |

### 2.2 进度只使用三种口径

- `OFFICIAL`：已经合并到 main，精确 Head 的 CI/Gate 全部通过。
- `CANDIDATE`：已经实现并测试，但仍位于 Draft PR 或本地分支。
- `COMPLETE`：本文件全部交付物和最终验收均已通过。

不得以提交数量、代码行数、页面可打开、接口返回成功或一次交易所订单成功代替完成度。

## 3. 执行组织与合并规则

### 3.1 双线路

| Lane | 负责范围 |
| --- | --- |
| Lane A — Safety / Execution | Entry convergence、Risk、Admission、Worker、Executor、Order recovery、Fill/Ledger、Reconciliation、故障注入 |
| Lane B — Data / Product | Gate read、Market Data、Backtest、Paper/Shadow、Strategy、Portfolio、Read API、Frontend、Operations |

共享的 Identity、Fingerprint、Schema、Migration、Transaction、Risk Contract 必须串行修改，不能由两条线同时改动。

### 3.2 每个 PR 的强制 Gate

- 独立分支、Draft PR、明确 base 与精确 Head。
- Backend CI、Security CI、适用的 PostgreSQL、Frontend build、OpenAPI、E2E 全部通过。
- `compileall`、`git diff --check`、Architecture Guard、Entry Guard、AI Boundary Guard 通过。
- 不提交凭证、日志、构建产物或 `docs/codex/`。
- 发现 raw DB exception、死锁、重复订单、孤儿事实、无法重放、Guard 增加或身份不一致时不得合并。
- 合并后同步 main、验证 tree parity，再清理分支和 worktree。

## 4. 从当前状态到彻底完成的工作明细

### Phase 0 — 当前成果收敛

| ID | 工作 | 交付物 | Definition of Done |
| --- | --- | --- | --- |
| F0-01 | 收口后端 PR #95 | 非实盘研究、回测、Paper/Shadow、只读 API 合并 main | 累计 Diff 审查；CI/Security 全绿；Live OFF；tree parity |
| F0-02 | 拆分本地集成分支 | Paper 生命周期、Gate 私有读取、统一快照、TestNet 适配、Fill/Ledger、健康 API、启动工具等独立 PR | 201 commits 被拆成可审查单元；无巨型直接合并；每个 PR 独立测试 |
| F0-03 | 收口前端 PR #1 | 中文 Dashboard 与真实只读 API 基础合并前端 main | 推送 8 个本地提交；build/test/E2E 通过；正式视图不静默伪装 Mock |
| F0-04 | 同步项目记录 | main、Guard、Entry baseline、PR 状态与进度一致 | 文档不再引用过时 SHA 或 46/31 作为当前数量；历史上限与实际值分开记录 |

### Phase 1 — Safety Core 16/16

#### SC-13 Entry-Point Convergence

| ID | 工作 | 交付物 | Definition of Done |
| --- | --- | --- | --- |
| SC13-01 | 完整入口 Inventory | REST、Manual、Strategy、Protection、Worker、Grid、Agent、MCP、脚本的路径/符号证据 | 所有可达入口被 Guard 扫描；没有未知入口 |
| SC13-02 | REST / Manual 收口 | Canonical Entry V2 → Durable Entry → Risk → Admission | Route 不调用 Executor/Exchange；caller-owned 单事务；typed rejection |
| SC13-03 | Strategy V2 收口 | Candidate Trade Plan → Admission | Strategy 不写 Legacy Pending Order、不持有凭证、不直接下单 |
| SC13-04 | Protection 收口 | 确定性 REDUCE/CLOSE/EMERGENCY_CLOSE/PROTECTION | `reduce_only=true`；target position 必填；不得增加风险 |
| SC13-05 | Restricted Sources 退役 | Agent/MCP/Grid/未批准 Venue 永久 DISABLED 或 410 | 不可通过环境变量重新启用；零交易副作用 |
| SC13-06 | Worker 输入收口 | Worker 只消费 Admission Durable Facts | 不消费 Legacy Queue；不绕过 Risk/Admission；Entry baseline 继续下降 |

#### SC-14 Read Cutover / G4-B

| ID | 工作 | 交付物 | Definition of Done |
| --- | --- | --- | --- |
| SC14-01 | Event Registry | Admission、Order、Fill、Ledger typed event parser | 未知 schema/version fail closed；事件身份确定性 |
| SC14-02 | Transactional Consumer | caller-owned、幂等 Outbox Consumer | 消费事实与游标同事务；DB 失败不确认消息 |
| SC14-03 | Projection Generation | Candidate generation、offset、event count、watermark、rebuild | offset 连续；完整性校验；伪造 generation 对象被拒绝 |
| SC14-04 | Shadow Diff | Legacy 与 Candidate 的 Decimal tolerance 比较 | 差异不可影响交易决策；policy snapshot 不可变 |
| SC14-05 | Reconciliation / Health | Checkpoint → HEALTHY/DEGRADED/UNHEALTHY | 异常时禁止增加风险，允许撤单/减仓/平仓 |
| SC14-06 | Read Cutover | API/Frontend 读取权威 Projection | stale/unavailable/unauthorized 明确；可回滚到旧读路径；G4-B 通过 |

#### SC-15 Legacy Retirement

| ID | 工作 | 交付物 | Definition of Done |
| --- | --- | --- | --- |
| SC15-01 | 旧交易真相退役 | Legacy Pending Queue、旧 Position/PnL、direct Executor/Exchange 失去权威角色 | 无可达旁路；Guard baseline 降至经审计的最低值 |
| SC15-02 | 数据迁移和孤儿审计 | 历史订单、成交、仓位、凭证 scope 映射报告 | 无无法解释的孤儿订单、重复 fill 或跨账户事实 |
| SC15-03 | 故障演练 | restart/rollback/replay/network/DB/late-fill/manual-trade/multi-strategy | 所有不变量成立；无重复经济订单；连接可恢复 |

SC-13、SC-14、SC-15 全部完成后，Safety Core 才能标记为 `16 / 16`。

### Phase 2 — Gate Spot / Perpetual 权威接入

| ID | 工作 | 交付物 | Definition of Done |
| --- | --- | --- | --- |
| GATE-01 | 环境与凭证 | TESTNET/CANARY/LIVE 环境合同、加密凭证、权限探测、轮换/删除 | Secret 仅后端解密；不回显、不入日志/Git；环境不能混用 |
| GATE-02 | Capability Matrix | Spot/Perpetual 的查询、Client ID、订单、成交、撤单、reduce-only 能力 | 每个能力有官方证据和契约测试；未知能力 fail closed |
| GATE-03 | Instrument Rules | tick/step/min qty/min notional/contract size/leverage/margin snapshot | 规则版本化；历史事件可按旧规则重放 |
| GATE-04 | Market Data | REST snapshot + WebSocket sequence + gap recovery | 去重、乱序、缺口、时间戳、stale health 测试通过 |
| GATE-05 | Account Facts | Spot 余额；Perpetual 仓位、保证金、杠杆、Funding、PnL | scope 完整；读取失败不伪装零值；统一健康结果 |
| GATE-06 | Rate Limit / Circuit Breaker | 限流、退避、熔断、恢复 | 429/5xx/timeout typed；不会误判 NOT_FOUND |

### Phase 3 — 数据、回测、Paper/Shadow 与策略

| ID | 工作 | 交付物 | Definition of Done |
| --- | --- | --- | --- |
| DATA-01 | PIT 数据集 | bars/trades/rules/funding 的不可变快照与 fingerprint | 无未来数据；缺失/重复/冲突可检测；同输入同结果 |
| BT-01 | 确定性回测 | next-open、费用、滑点、Funding、部分成交、杠杆、清算、Walk-forward | 报告可重放；计算均为 Decimal；参数和数据版本完整 |
| PS-01 | Paper / Shadow | Durable order/fill/event/checkpoint/recovery | 与 TestNet 共用 Entry/Risk/Order/Fill 语义；重启可恢复 |
| STRAT-01 | 策略 Runtime | 调度、版本化参数、输入 snapshot、Candidate Trade Plan | 策略不直接下单；失败不产生半完成交易事实 |
| STRAT-02 | 内建策略库 | SMC、ICT、Trend Following、Mean Reversion | 每个策略有回测、Paper、Shadow 证据和拒绝原因 |
| PORT-01 | Position Sizing | 用户杠杆、保证金模式、gross/net/instrument exposure | 不静默改写用户配置；超限 typed deny |
| RISK-01 | Portfolio / Cooldown | 日损、回撤、连续亏损、Funding 成本、全局/账户/策略 Kill Switch | 跨重启；降低风险动作始终可用；增加风险 fail closed |

### Phase 4 — Durable Trading Runtime 与 TestNet 全生命周期

| ID | 工作 | 交付物 | Definition of Done |
| --- | --- | --- | --- |
| EXEC-01 | Durable Worker | lease、claim、heartbeat、retry、dead-letter、restart recovery | 只消费 Admission Outbox；不重复处理；不直接生成业务身份 |
| EXEC-02 | Submission Attempt | 提交前持久化 attempt、request fingerprint、deterministic client ID | READY→SUBMITTING→ACKED/UNKNOWN/REJECTED 合法转换 |
| EXEC-03 | Gate Spot Orders | Market、Limit、Stop Market、Stop Limit、Cancel | 买/卖、部分成交、撤单、late fill、费用完整测试 |
| EXEC-04 | Gate Perpetual Orders | Open/Increase/Reduce/Close/Close All/Protection | long/short、reduce-only、position side、leverage/margin 完整 |
| EXEC-05 | Submission Unknown | timeout/crash/response-before-DB/query recovery | 未查询交易所前禁止重发；无双重经济订单 |
| EXEC-06 | Cancel / Replace | CANCEL_REQUESTED→CANCELLING→CANCELLED | cancel timeout/reject/late fill；补单不 overfill |
| EXEC-07 | Fill Ingestion | REST/WebSocket/history backfill 去重 | stable fill key；跨账户/市场/标的不碰撞；fallback 冲突隔离 |
| EXEC-08 | Fee / Funding | 多资产 fee、valuation evidence、Funding | 不猜汇率；第三资产保留；估值证据不可变 |
| EXEC-09 | Atomic Settlement | Fill→Ledger→Order checkpoint→Projection→Reconciliation→Outbox | 单事务；失败全部回滚；消费端幂等 |

### Phase 5 — 前端完整产品

| ID | 工作 | 交付物 | Definition of Done |
| --- | --- | --- | --- |
| FE-01 | 认证与账户连接 | 登录、Gate 凭证创建/测试/删除、环境选择 | Secret 不回显；浏览器不直连 Gate；权限/网络错误清晰 |
| FE-02 | 账户和市场 | Spot/Perpetual 余额、仓位、保证金、行情、健康状态 | stale/unavailable 明确；不以 Mock 冒充真实数据 |
| FE-03 | 研究工作台 | 数据集、回测、参数、报告、策略目录 | 可复现 fingerprint；结果可下载和审计 |
| FE-04 | Paper/Shadow/TestNet | 环境切换、策略运行、订单、成交、PnL | 不允许环境混用；每个状态有文字与颜色双重表达 |
| FE-05 | Risk / Admission | Kill Switch、Decision、Reservation、拒绝原因、Reconciliation | 风险降低操作可见；Hard Risk 不可由前端覆盖 |
| FE-06 | Order Lifecycle | Market/Limit/Stop/Cancel、状态机、UNKNOWN recovery | 所有写操作走后端 Admission；无直接 Exchange 调用 |
| FE-07 | Operations | Outbox lag、Projection、Shadow Diff、Health、告警、审计时间线 | 运维人员可以定位失败阶段和 correlation，不暴露 secret |
| FE-08 | Frontend E2E | 登录→连接→研究→Paper→TestNet→订单→对账 | 1440/1920 响应式；build/lint/unit/E2E 全绿 |

### Phase 6 — 生产部署与运维

| ID | 工作 | 交付物 | Definition of Done |
| --- | --- | --- | --- |
| OPS-01 | 部署拓扑 | API、Worker、Consumer、Scheduler、PostgreSQL 分离运行 | 服务依赖、启动顺序、健康检查和 graceful shutdown 明确 |
| OPS-02 | Migration | expand-only、backfill、shadow write/read cutover、rollback | 空库和升级库一致；迁移可重放；不丢订单 |
| OPS-03 | Secret / Access | Secret store、最小权限、轮换、吊销、审计 | 无明文 secret；泄漏演练和轮换流程通过 |
| OPS-04 | Observability | metrics、structured logs、trace、alerts、dashboard | 覆盖 order age、UNKNOWN、outbox lag、projection lag、reconciliation、PnL mismatch |
| OPS-05 | Backup / Restore | PostgreSQL 备份、恢复、PITR、ledger rebuild | 恢复演练后账本、仓位、订单和 checkpoint 一致 |
| OPS-06 | Deployment / Rollback | 蓝绿或滚动发布、版本兼容、自动回滚 | 发布中不丢 durable command；旧版本可安全退出 |
| OPS-07 | Security | CodeQL、dependency/source audit、secret scan、auth/rate-limit/E2E | 无未处理 P0/P1；生产配置无默认弱密钥 |

### Phase 7 — 系统级验收与正式运行

| ID | 工作 | 交付物 | Definition of Done |
| --- | --- | --- | --- |
| REL-01 | 全量自动化 | Backend、PostgreSQL、Frontend、OpenAPI、Security、Guard、E2E | 精确 release Head 全绿；无 flaky P0/P1 |
| REL-02 | Paper/Shadow Soak | 连续运行证据和事件重放报告 | 至少 7 天或等价事件量；零无法解释差异 |
| REL-03 | TestNet Soak | Spot/Perpetual 全订单类型与故障恢复证据 | 至少 72 小时；订单/Fill/Ledger/Position/Reconciliation 一致 |
| REL-04 | Fault Injection | crash、timeout、429、5xx、DB outage、duplicate、late fill、host restart | 不变量全部成立；无重复经济订单或孤儿事实 |
| REL-05 | Canary | 单账户、单策略、单/少量标的、极低风险额度、人工值守 | Kill Switch 和 rollback 验证；无未解释资金/仓位差异 |
| REL-06 | Production Deployment | 正式域名、TLS、数据库、Worker、监控、备份、值守手册 | 生产环境可启动、可停止、可恢复、可审计 |
| REL-07 | Controlled Live Gate | 人工确认账户、额度、策略、标的、最大亏损、紧急流程 | 软件具备 Live 能力；Live 只能由授权人员显式开启 |
| REL-08 | Final Sign-off | 最终验收报告与版本标签 | 第 9 节全部勾选；标记 `PROJECT_COMPLETE` |

## 5. 环境晋级规则

环境集合固定为：

```text
DISABLED → PAPER → SHADOW → TESTNET → CANARY → LIVE
```

| 晋级 | 必须证据 |
| --- | --- |
| DISABLED → PAPER | unit/PostgreSQL/Guard 全绿；无外部交易调用 |
| PAPER → SHADOW | 策略、Risk、Admission、Ledger 可重放；Paper 恢复通过 |
| SHADOW → TESTNET | Shadow Diff 在 tolerance 内；Reconciliation HEALTHY |
| TESTNET → CANARY | Spot/Perpetual 全生命周期、重启、超时、撤单、late fill、费用/Funding 通过 |
| CANARY → LIVE | Canary 无未解释差异；Kill Switch/rollback 有效；人工批准账户和额度 |

不得跳级，任何环境不得由策略、Agent、LLM 或单一环境变量隐式提升。

## 6. 系统级测试矩阵

最终 release 至少覆盖：

- Decimal、精度、状态机、fingerprint、scope、typed error 单元测试。
- PostgreSQL schema、FK、unique、append-only、CAS、并发、rollback、deadlock retry。
- REST/WebSocket 重复、乱序、缺口、timeout、401/403、429、5xx。
- Market/Limit/Stop/Cancel、partial fill、overfill、late fill、submission unknown。
- Spot 与 Perpetual 的 long/short、reduce-only、close-all、leverage、margin、fee、Funding。
- Worker/Consumer 主机重启、数据库中断、交易所中断、消息未发布、游标未推进。
- 回测/Paper/Shadow/TestNet 相同输入和策略版本的一致性。
- 手动交易、多策略共享账户、跨账户/标的 scope 冲突。
- 前端 auth、credential、research、order、position、risk、reconciliation、operations E2E。
- migration 空库、升级库、重复执行、回滚、备份恢复和 ledger rebuild。
- Secret scan、dependency audit、CodeQL、权限、限流和日志脱敏。

## 7. 安全与资金边界

- API Key/Secret 只能由后端加密保存；不得进入源代码、前端、截图、日志、测试 fixture 或 PR 描述。
- 已在对话或截图中暴露的凭证必须作废并重新生成，不能进入 Canary 或 Live。
- TestNet 凭证和 Live 凭证必须物理区分，不能自动回退或交叉使用。
- 任何真实资金操作都需要账户、标的、方向、最大订单、最大日损、最大回撤和 Kill Switch 的人工确认。
- AI/LLM/Agent Trading Authority 固定为 0%；其输出不能决定 action、side、quantity、leverage、risk budget 或 Admission。

## 8. 进度计算与汇报

总进度按第 4 节工作包计算，只有达到 DoD 并合并 main 的工作包进入 Official Progress。

```text
Official Progress = 已完成并合并工作包权重 / 全部工作包权重
Candidate Progress = 已通过本地或 Draft Gate 的工作包权重 / 全部工作包权重
```

里程碑汇报点固定为：

1. Phase 0 收敛完成。
2. Safety Core 16/16。
3. Gate TestNet 完整交易闭环。
4. 策略/回测/Paper/Shadow 语义统一。
5. 前端与运维生产化完成。
6. Canary 完成。
7. `PROJECT_COMPLETE` 最终签署。

## 9. 最终验收清单

以下全部勾选后，项目即视为彻底完成，不再存在隐藏的后续主体开发阶段：

### Code / Architecture

- [ ] 所有批准代码已经合并 main，工作分支和 Draft PR 已收口。
- [ ] Safety Core 16/16。
- [ ] Order/Entry/AI Guard 无新增旁路，遗留基线已退役或有正式豁免证据。
- [ ] 所有入口统一经过 Canonical Entry → Risk → Admission → Outbox → Worker。
- [ ] 无 Legacy Queue、direct Executor/Exchange 或重复事实权威路径。

### Gate / Trading

- [ ] Gate Spot 与 Perpetual 账户、行情、规则和权限读取通过。
- [ ] Spot Market/Limit/Stop/Cancel 完整通过。
- [ ] Perpetual Market/Limit/Stop/Cancel/Open/Reduce/Close/Close All 完整通过。
- [ ] leverage、margin mode、reduce-only、position side、fee、Funding 完整通过。
- [ ] submission unknown、cancel timeout、partial fill、late fill、重复 fill 和重启恢复通过。

### Ledger / Risk / Reconciliation

- [ ] Fill、Ledger、Position、PnL、Fee、Funding 全链路原子且可重放。
- [ ] Kill Switch、Reservation、日损、回撤、敞口、保证金、Cooldown 全部生效。
- [ ] Reconciliation 可发现手动交易、跨系统差异和孤儿事实。
- [ ] 对账不健康时禁止增加风险，允许撤单、减仓和平仓。

### Research / Strategy

- [ ] PIT 数据、回测、Paper、Shadow、TestNet 使用同一策略和风险语义。
- [ ] SMC、ICT、Trend、Mean Reversion 均有确定性回测和拒绝证据。
- [ ] 策略只输出 Candidate Trade Plan，不拥有交易权限。
- [ ] 多策略共享账户的仓位、风险预算和冲突控制通过。

### Frontend

- [ ] 登录、凭证、账户、市场、研究、回测、策略、订单、仓位、风险、对账和运维页面完成。
- [ ] 正式页面不依赖 Mock；stale/unavailable/unauthorized 清晰显示。
- [ ] 所有写操作走 Admission；浏览器不直连交易所、不持有 Secret。
- [ ] Frontend unit/build/lint/E2E 和 1440/1920 视觉验收通过。

### Operations / Release

- [ ] migration、部署、回滚、监控、告警、备份和恢复演练通过。
- [ ] Backend、PostgreSQL、Frontend、OpenAPI、Security、Guard、E2E 全绿。
- [ ] Paper/Shadow soak、TestNet soak 和 Fault Injection 完成。
- [ ] Canary 在限定额度下完成，零未解释订单、仓位或资金差异。
- [ ] 生产环境运行手册、值守、紧急撤单/平仓、Kill Switch 和事故响应完成。
- [ ] 发布版本打 Tag，最终验收报告签署。

### Final State

- [ ] 系统状态标记为 `PROJECT_COMPLETE`。
- [ ] 系统具备 `CONTROLLED_LIVE_READY` 能力。
- [ ] Live 是否开启由最终人工运营授权决定；该决定不会改变“软件开发已完成”的结论。

## 10. 终局结论

本文件覆盖从当前基线到正式运行的全部主体开发、集成、测试、部署、恢复、Canary 和受控 Live 准备。只有第 9 节全部通过，才能宣布整个项目完工；此后剩余事项只能是正常运营、策略迭代或新产品扩展，不再属于本次项目建设缺口。
