# QuantDinger 策略引入 — 阶段 P0-A 研究审计报告

**日期**: 2026-08-06
**审计范围**: LJ0930l-beep/QuantDinger, branch `full-live-product-integration`
**审计方式**: 只读，未修改任何生产代码
**依据**: 《QuantDinger GitHub 策略引入与落地实施方案》v1.0

---

## 第一部分：参考仓库许可证审计

| 仓库 | 许可证 | 已核实 | QuantDinger 使用方式 |
|------|--------|--------|---------------------|
| jesse-ai/jesse | MIT | ✅ via raw `__init__.py` headers | 可参考并独立重写策略指标计算（保留版权声明） |
| vnpy/vnpy_ctastrategy | MIT | ✅ `LICENSE` raw | 可参考 CTA 规则模板，保留版权声明 |
| freqtrade/freqtrade-strategies | GPL-3.0 | ✅ repo badge + LICENSE | **仅研究思想**；禁止复制代码实现 |
| iterativv/NostalgiaForInfinity | GPL-3.0 | ✅ repo badge + LICENSE | **仅研究公开思路**；不得移植条件树/变量名/参数集 |
| nateemma/strategies | GPL-3.0 | ✅ repo badge | **仅研究因子和验证方法** |
| hummingbot/hummingbot | Apache-2.0 | ✅ `LICENSE` raw, Copyright Hummingbot Foundation | 可研究做市模型；需保留许可证和修改声明 |
| nkaz001/hftbacktest | MIT | ✅ `LICENSE` raw, Copyright nkaz001 | 第二阶段研究（订单簿/队列/延迟模型） |

**结论**: MIT 仓库可以借鉴思想并独立重写（保留版权）。GPL-3.0 仓库仅做思想研究，不得复制任何代码行。Apache-2.0 可以研究模型，重写时需保留许可证声明。

---

## 第二部分：现有 15 策略去重分析

### 2.1 去重矩阵

策略分为三大族：

**EMA 趋势族** (s02, s04, a07):
- s02: EMA(12/26/9) + Volume SMA(20)
- s04: MACD(12/26/9) + Histogram strength
- a07: EMA(5/12/26) + ATR(14) dynamic sizing
- **重复度**: 中等（共享 EMA 族指标和 crossover 逻辑，参数和确认条件不同）
- **建议**: 合并为一个参数化 EMA Crossover 策略

**Donchian/通道突破族** (a01, a02, a03):
- a01: Turtle (Donchian 20/10 + 4-unit pyramiding + ATR(20))
- a02: Dual Thrust (21, K=0.7 + ATR(14))
- a03: Donchian (20 + channel width > 2% + ATR(14))
- **重复度**: 高（a01 和 a03 共享 Donchian(20) 入场；a02 逻辑不同但同属突破族）
- **建议**: 合并为参数化通道突破策略

**超买超卖 RSI 族** (s01, s03):
- s01: BB(20,2) + RSI(14) + ATR(14)
- s03: RSI(7) + RSI(14) + BB width filter + ATR(14)
- **重复度**: 高（双 RSI 确认 + BB bands 过滤）
- **建议**: 合并为参数化 RSI+BB 均值回归策略

**完全重复**: `s05_triple_ema` 与 `a07_triple_ema` — 两个不同文件名的相同策略，必须去重。

### 2.2 缺失的关键能力

| 能力 | 状态 | 影响 |
|------|------|------|
| Market Regime 检测/过滤 | ❌ 缺失 | 15 策略均无市场状态过滤 |
| 回测中的 Funding Rate 建模 | ❌ 缺失 | `fundingMode: "not_modeled"` |
| Hedge Admission / 双腿状态机 | ❌ 缺失 | 无 NEUTRAL-01 基础 |
| 策略参数化 (context.params) | ⚠️ 预留但未使用 | 15 策略将参数硬编码在代码中 |
| 跨币种多标测试 | ⚠️ 支持但不完整 | 多数策略仅用 BTC 测试过 |

### 2.3 策略数据流安全性

✅ 策略通过 `context.order(symbol, qty)` 间接下单（不是直接交易所 API）
✅ 回测引擎保证 Point-in-Time（`set_clock(include_current=False)`）
✅ Paper 路径经过统一 Hard Risk 准入（`RuntimeEntryAdmissionService`）
✅ Live 路径有 direction guard（`validate_strategy_signal_direction`）

---

## 第三部分：基础设施就绪状态

| 组件 | 就绪 | 备注 |
|------|------|------|
| Strategy V2 编译/验证 | ✅ | `contract.py` AST 验证 |
| PIT 回测引擎 | ✅ | `set_clock(include_current=False)` |
| Next-Open 成交模型 | ✅ | `MultiAssetSimulationBroker` |
| Paper 风险准入 | ✅ | `RuntimeEntryAdmissionService` + Hard Risk |
| 保护止损引擎 | ✅ | `ProtectionEngine` (SL/TP/Trailing/TimeLimit) |
| PIT Dataset 指纹 | ✅ | `PITDatasetService.compute_fingerprint()` |
| Market Regime Provider | ❌ | 需新建 domain contract |
| Funding Rate 回测建模 | ❌ | 需在回测引擎添加 funding cost |
| Hedge Admission 双腿 | ❌ | 需新建 HedgeAdmissionGate |
| Walk-forward 框架 | ❌ | 需新建训练/验证/测试滚动窗口工具 |

---

## 第四部分：6 策略 vs 现有能力对照

| 方案策略 | 现有可映射？ | 差距 |
|----------|-------------|------|
| SPOT-01 Donchian+ATR 趋势突破 | a01 部分覆盖 | 缺 EMA 趋势过滤、缺成交量过滤、缺 Regime 开关、缺时间退出 |
| SPOT-02 Bollinger+RSI+Regime | s01/s03 部分覆盖 | **缺 Regime 过滤**（核心要求）、缺 Cooldown、缺闪崩保护 |
| SPOT-03 NFI-Lite 多TF超跌 | s05 部分覆盖 | 缺多时间框架、缺 BTC 基准急跌保护、缺 reason_codes 输出 |
| FUT-01 Turtle 双向趋势 | a01 部分覆盖 | **缺 Funding guard**、缺 reduce_only 映射、缺 evidence_version |
| FUT-02 Supertrend+EMA+ADX | a04 部分覆盖 | 缺 EMA 趋势确认、缺价差过滤、缺抗震荡静默 |
| NEUTRAL-01 现货-永续中性 | ❌ 完全不存在 | 需建 Hedge Admission、双腿状态机、失败补偿、Delta 监控 |

---

## 第五部分：建设建议

### 5.1 重构 > 删光重写

现有策略的指标计算（EMA、RSI、ATR、Bollinger、MACD、Donchian、PSAR、SuperTrend、MFI）都已经实现且经过 sandbox 执行测试。建议**提取公共指标层**，然后按方案规格重构策略逻辑。

### 5.2 基础设施优先（P0-B 前置）

在写任何策略代码之前，必须先完成：
1. **MarketRegimeProvider** — Regime 枚举 + 检测器（ADX/ATR pct 组合）
2. **Strategy Evidence 合同** — `StrategySignalFact` dataclass（见第六部分设计）
3. **Funding Rate 数据源** — 用于 FUT-01/02 的回测建模

### 5.3 策略实现顺序

P1-P3 建议按依赖关系排列：SPOT-01/02 → SPOT-03 → FUT-01/02 → NEUTRAL-01

### 5.4 Guard Baseline 承诺

以下项保证不增加：
- Architecture Guard baseline: 46（当前值）
- Entry-Point Side-effect count: 37（legacy）
- Order Side-effect count: 不变
- AI Boundary: 不增加新的 HTTP/WebSocket 通路

---

## 第六部分：统一 Strategy Signal Evidence 合同设计（草案）

```python
"""Domain-level strategy evidence contracts. Pure dataclasses — no infrastructure imports."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class StrategyAction(str, Enum):
    NO_ACTION = "no_action"
    ENTRY = "entry"
    ADD = "add"
    REDUCE = "reduce"
    CLOSE = "close"


class PositionSide(str, Enum):
    NET = "net"
    LONG = "long"
    SHORT = "short"


class MarketRegime(str, Enum):
    """Regime classification for strategy filtering."""
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGING = "ranging"
    HIGH_VOLATILITY = "high_volatility"
    LOW_VOLATILITY = "low_volatility"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class StrategySignalFact:
    """Unified, immutable strategy signal evidence.

    All strategies MUST output this (or a list of these) per bar.
    NO_ACTION is a valid result — rejected_reason_codes must explain why.
    """
    strategy_id: str
    strategy_version: str
    parameter_fingerprint: str
    dataset_snapshot_id: str

    # Instrument
    instrument_id: str
    market_type: str               # "spot" | "swap"
    timeframe: str                  # "5m" | "15m" | "1h" | "4h"

    # Action
    action: StrategyAction = StrategyAction.NO_ACTION
    position_side: PositionSide = PositionSide.NET
    confidence: float = 0.0         # 0.0–1.0, optional signal strength

    # Price references (ALL from closed bars only)
    entry_reference_price: float = 0.0
    stop_reference_price: float = 0.0
    target_reference_price: float = 0.0
    risk_distance: float = 0.0      # in price units, for sizing

    # Human-readable audit trail
    reason_codes: tuple[str, ...] = ()
    rejected_reason_codes: tuple[str, ...] = ()

    # Deterministic evidence
    evidence_fingerprint: str = ""
    observed_at: str = ""           # ISO-8601 UTC
    bar_close_time: str = ""        # ISO-8601 UTC; MUST NOT be later than backtest decision time

    # Regime context (set by regime provider, NOT strategy)
    regime: MarketRegime = MarketRegime.UNKNOWN
    regime_details: dict[str, Any] = field(default_factory=dict)

    # Metadata (extensible)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CandidateTradePlan:
    """A validated trade plan ready for sizing + admission.

    Generated by the execution layer from one or more StrategySignalFacts.
    Must NOT contain exchange credentials, order IDs, or HTTP handles.
    """
    signal_facts: tuple[StrategySignalFact, ...]
    instrument_id: str
    market_type: str
    action: StrategyAction
    position_side: PositionSide
    quantity: float                  # base quantity (before sizing)
    stop_price: float
    target_price: float

    # Admission required fields
    credential_id: int
    idempotency_key: str
    reduce_only: bool = False

    # For hedge strategies (NEUTRAL-01)
    hedge_group_id: str = ""
    leg_index: int = 0               # 0 or 1
    pair_instrument_id: str = ""
    delta_target: float = 0.0        # target portfolio delta
```

### 合同设计原则

1. **纯 Domain**: 不导入数据库、HTTP、WebSocket、CCXT、密钥等基础设施
2. **frozen dataclass**: 不可变，支持 hash + fingerprint 计算
3. **NO_ACTION 是合法结果**: 拒绝时填 `rejected_reason_codes`
4. **所有时间为 UTC**: `observed_at` / `bar_close_time` 显式标记
5. **所有价格为 close_bar**: 不允许使用当前未闭合 K 线的数据
6. **Decimal 原则**: 经济事实使用 Decimal，合同层可用 float 做序列化接口

---

## 第七部分：下一步建议

1. **审查**: 审核此审计报告，确认去重矩阵和缺口分析
2. **决策**: 批准/修改 6 策略范围和优先级
3. **P0-B**: 实现 MarketRegimeProvider + Strategy Evidence 合同（公共 PR，不改策略）
4. **P1**: 实现 SPOT-01 Donchian+ATR（第一个 PR，建立模式）
5. **P2-P3**: SPOT-02/03 → FUT-01/02 → NEUTRAL-01

---

*本报告基于 2026-08-06 的 QuantDinger `full-live-product-integration` 分支生成。*
*许可证信息基于仓库的公开 LICENSE 文件。仓库内容可能随时间变化。*
