# QuantDinger 完整开发路线图

> 基线：2026-08-05 19:40，Safety Core 14/16，全量 2335 passed，PR #96 OPEN

---

## 总览

```
已完成 ████████████████░░░░░░░░░░░░  ~65%
         Safety Core  策略/前端  运维/验收
         14/16        未开始     未开始
```

| 阶段 | 状态 | 预估 |
|---|---|---|
| Phase A — SC-15 收口 | 🔴 今天 | 1h |
| Phase B — 策略库 | ⚪ 待开始 | 3-5d |
| Phase C — 前端完整产品 | ⚪ 待开始 | 3-5d |
| Phase D — 数据与回测 | ⚪ 待开始 | 2-3d |
| Phase E — 运维与部署 | ⚪ 待开始 | 2-3d |
| Phase F — 系统验收 | ⚪ 待开始 | 7-14d |

---

## Phase A — SC-15 Legacy Retirement 收口（今天，~1h）

### A1. Tier 3: pending_order_worker.py 退役（~30min）

**目标**：bypass 基线 9→1，Safety Core 14→15/16

**方法**：用 sed 按 AST 精确定位的行号删除死代码体，并对 4 个子方法注入终端 guard：

| 方法 | 行范围 | 操作 |
|---|---|---|
| `_execute_alpaca_order` | 2498-2674 | 保留 def 签名 + docstring，注入 `raise RuntimeError` + pass |
| `_execute_ibkr_order` | 2331-2496 | 同上 |
| `_sync_one_quick_trade_order` | 281-418 | 同上 |
| `_attach_native_protection` | 1055-1121 | 同上 |
| `start` | 188-202 | 清理 terminal return 后的死代码 |
| `_tick` | 219-245 | 同上 |

**验证**：
- `test_sc15_terminal_guard_proof.py` 3/3
- `test_entrypoint_convergence_guard.py` 10/10（bypass 9→1）
- 全量 pytest（排除已知 GateAuthFacts 污染）

### A2. SC-15 最终收尾（~20min）

1. `routes/quick_trade.py`：检查 `kill_switch` 函数是否需要终端 guard
2. `mcp_server/`：确认 `cancel_open_paper_orders` 终端 guard 存在
3. 更新 `FINAL_ACCEPTANCE_REPORT.md`：SC-15 DONE
4. 更新 `CORE_ROADMAP.md`：Safety Core 15/16
5. 更新 `final_acceptance_evidence.json`：bypass 基线 1

**产出**：Safety Core 15/16，bypass 基线 1

### A3. 可选：孤儿表标记（~10min）

为 ~90 张未使用 `qd_*` 表添加退役注释（DDL 注释或文档），标记为 "SC-15 retired"。

---

## Phase B — 内建策略库 STRAT-02（3-5 天）

### B1. 策略框架（1d）

- 策略基类：`BaseStrategy`，标准化 `generate_candidate(pipeline_snapshot) → CandidateTradePlan`
- 参数版本化：每个策略参数有 fingerprint
- 策略不直接下单，只产 CandidateTradePlan

### B2. 四个策略实现（2d）

| 策略 | 核心逻辑 |
|---|---|
| **SMC (Smart Money Concepts)** | 订单块、流动性抓取、市场结构突破 |
| **ICT (Inner Circle Trader)** | FVG、OTE、killzone 时间窗口 |
| **Trend Following** | 多时间框架 EMA/SMA 交叉 + ADX 确认 |
| **Mean Reversion** | 布林带 + RSI 超买超卖 + 成交量确认 |

### B3. 回测验证（1d）

- 每个策略至少 6 个月历史数据 walk-forward
- PIT 数据集确保无未来数据
- 输出：Sharpe、max drawdown、win rate、profit factor

### B4. Paper/Shadow 证据（1d）

- 每个策略在 Paper 环境运行 200+ bar
- Shadow diff 与 TestNet 对比
- Reconciliation HEALTHY

---

## Phase C — 前端完整产品（3-5 天）

### C1. FE-04: Paper/Shadow/TestNet 环境切换（1.5d）

- 环境选择器（DISABLED → PAPER → SHADOW → TESTNET）
- 策略运行状态面板
- 订单/成交/PnL 实时列表
- 环境混用防护（颜色+文字双重表达）

### C2. FE-05: Risk / Admission 面板（1d）

- Kill Switch 开关（GLOBAL/ACCOUNT/STRATEGY 三级）
- Risk Decision 时间线（ALLOW/DENY 原因）
- Reservation 状态
- Reconciliation 健康面板

### C3. FE-07: Operations 运维面板（1d）

- Outbox lag 监控
- Projection 管线状态
- Shadow Diff 仪表盘
- 审计时间线（correlation_id 追踪）

### C4. 响应式验证（0.5d）

- 1440px / 1920px 断点测试
- build + lint + unit + E2E 全绿

---

## Phase D — 数据与回测（2-3 天）

### D1. DATA-01: PIT 数据集（1.5d）

- bars/trades/rules/funding 不可变快照
- SHA-256 fingerprint
- 缺失/重复/冲突自动检测
- 同输入同结果保证

### D2. BT-01: 确定性回测（1d）

- next-open 执行模型
- 费用/滑点/Funding/部分成交/杠杆/清算建模
- Decimal 精度
- Walk-forward 框架

### D3. PORT-01/RISK-01: 仓位与风控完善（0.5d）

- 用户杠杆/保证金模式校验
- gross/net/instrument exposure 上限
- 日损/回撤/连续亏损/冷却期
- 跨重启持久化

---

## Phase E — 运维与部署（2-3 天）

### E1. OPS-02: Migration 脚本（0.5d）

- expand-only 迁移验证
- 空库→升级库一致性
- 可重放性测试

### E2. OPS-03: Secret 审计（0.5d）

- 全仓库扫描：无明文 secret
- 最小权限原则验证
- 轮换流程文档

### E3. OPS-04: 可观测性（1d）

- Prometheus metrics（order_age, UNKNOWN_count, outbox_lag, projection_lag, reconciliation_mismatch, PnL_diff）
- 结构化日志（JSON 格式）
- Grafana 仪表盘模板

### E4. OPS-05/06: 备份恢复 + 部署（1d）

- PostgreSQL backup/restore/PITR 脚本
- ledger rebuild 流程
- 蓝绿/滚动发布方案
- 自动回滚条件

---

## Phase F — 系统验收（7-14 天，需时间/授权）

### F1. REL-01: 全量自动化（0.5d）

- 全量 CI 管道：Backend + PostgreSQL + Frontend + OpenAPI + Security + Guard + E2E
- release Head 全绿

### F2. REL-02: Paper Soak（7d）

- 连续运行 7 天或等价事件量
- 零无法解释差异

### F3. REL-03: TestNet Soak（72h）

- 需 `GATE_TESTNET_WRITE_ENABLED=1`
- Spot/Perpetual 全订单类型
- 故障恢复证据

### F4. REL-04: 故障注入（1d）

- crash、timeout、429、5xx、DB outage、duplicate、late fill、host restart
- 不变量全成立

### F5. REL-05/06: Canary + 生产（需授权）

- 单账户、单策略、极小额度
- 人工值守 + Kill Switch 验证
- 正式域名、TLS、监控、备份

### F6. REL-07/08: Live + Sign-off（需审批）

- 人工确认账户、额度、策略、标的、最大亏损
- `PROJECT_COMPLETE` 标签

---

## 环境晋级路径

```
DISABLED ──→ PAPER ──→ SHADOW ──→ TESTNET ──→ CANARY ──→ LIVE
   ✅          ✅         ✅          ⬜          ⬜          ⬜
 (当前)     (代码就绪)  (代码就绪)  (代码就绪)  (需授权)    (需审批)
```

| 晋级 | 当前状态 | 缺口 |
|---|---|---|
| PAPER | ✅ 代码就绪 | — |
| SHADOW | ✅ 代码就绪 | Shadow Diff 7d 证据 |
| TESTNET | ⚠️ 代码就绪 | Soak 72h + 写权限开启 |
| CANARY | ❌ 未开始 | 单账户授权 + 人工值守 |
| LIVE | ❌ 未开始 | 全面审批 |

---

## 最终里程碑

| 里程碑 | Safety Core | 测试 | 状态 |
|---|---|---|---|
| M1 — SC-15 DONE | 15/16 | bypass 9→1 | ⬜ 今天 |
| M2 — 策略库 DONE | 15/16 | 4 策略回测+Paper | ⬜ |
| M3 — 前端 DONE | 15/16 | FE-01~08 全绿 | ⬜ |
| M4 — 部署就绪 | 15/16 | OPS-01~07 | ⬜ |
| M5 — Soak 通过 | 16/16 | 7d+72h 零差异 | ⬜ |
| M6 — PROJECT_COMPLETE | 16/16 | §9 全勾 | ⬜ |

---

## 下一步

**立即**：Phase A — SC-15 Tier 3（~1h），Safety Core → 15/16
