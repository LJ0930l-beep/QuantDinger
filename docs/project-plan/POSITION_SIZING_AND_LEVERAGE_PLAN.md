# Position Sizing / Leverage / Margin Plan

## 用户选择优先

正式字段为 `selected_leverage`，由用户在前端明确填写并形成不可变配置快照。系统不得自动降杠杆、静默改写、沿用上一配置或按波动率自动调节；策略不得选择杠杆。

支持配置层级：Instrument + Direction > Instrument > Strategy Default；手动订单使用自身 `selected_leverage`。快照至少含 strategy、instrument、direction、selected_leverage、effective_from、selected_by/at、configuration_version、idempotency_key。

## Gate 同步

OPEN/INCREASE 前：读取配置 → 读取 Gate 当前杠杆 → 不一致时执行独立、幂等、可审计同步 → 再读验证 → 持久化 reconciliation fact → Hard Risk → Admission。失败返回 `LEVERAGE_SYNC_FAILED`、Health=UNHEALTHY，不提交订单。

Gate 不支持用户选择值时返回 `LEVERAGE_NOT_SUPPORTED`，不得自动使用较低值。

## 保证金成本

模式：`ACCOUNT_EQUITY_PERCENT` 或 `FIXED_MARGIN`。目标名义价值 = 目标保证金成本 × 用户选择杠杆；数量再按乘数、价格、最小数量、步长、风险档位和仓位模式确定。向上取整导致实际保证金超过用户目标时拒绝，不超额替用户开仓。
