"""
新用户注册时写入内置示例指标（可自由修改、删除）。

通过首条示例名称做幂等：已存在则跳过，避免重复调用 create_user 等边界情况重复插入。
"""

from __future__ import annotations

import time
from typing import Any, Dict, List

from app.utils.logger import get_logger

logger = get_logger(__name__)


# Used for idempotency on registration. Keep in sync with _builtin_specs()[0]["name"].
_BUILTIN_PACK_ANCHOR_NAME = "[Sample] SuperTrend Trend-Following"
_LEGACY_EXECUTION_TOKENS = (
    "open_long",
    "close_long",
    "open_short",
    "close_short",
    "# @strategy",
    "exit_owner",
    "signal_form",
)
_LEGACY_BUILTIN_SAMPLE_NAMES = (
    _BUILTIN_PACK_ANCHOR_NAME,
    "[示例] 布林带触及",
    "[示例] MACD 柱穿零轴",
    "[示例] 双均线策略",
    "[示例] RSI 超买超卖",
    "Dual Moving Average Strategy",
)


# QuantDinger Indicator IDE contract (the sandbox injects df / pd / np / params):
#   * top of file declares my_indicator_name / my_indicator_description
#   * df = df.copy()  -> work on a private copy
#   * output['signals'] is chart-only and never drives orders by itself
#   * output dict contains plots / signals; every data list MUST have length == len(df)
#   * # @param ... range=a:b:s  auto-detected by the structured parameter tuner
_SUPERTREND_CODE = r'''# ============================================================
# [Sample] SuperTrend Trend-Following -- classic ATR channel flip
# --- QuantDinger chart indicator contract ---
# ------------------------------------------------------------
# Idea: build an adaptive band pair (HL2 +/- mult * ATR). The bands
# can only tighten in the prevailing trend direction. Price crossing
# the opposite band flips visual direction and fires chart markers.
#
# Design notes:
#   1) ATR uses Wilder smoothing (ewm alpha=1/N) so values match
#      TradingView and most pro charting tools.
#   2) Final upper / lower bands are path-dependent: they cannot
#      drift in the unfavourable direction, so we recurse bar by bar
#      in a Python loop instead of pure vectorisation.
#   3) Signals fire on the bar where direction flips -- naturally
#      edge-triggered, no repeat entries while the trend persists.
#   4) Direction is compared against the PREVIOUS final band only
#      (cl[i] vs final_*[i-1]) -> strictly no look-ahead bias.
# ============================================================

my_indicator_name = "[Sample] SuperTrend Trend-Following"
my_indicator_description = (
    "Classic SuperTrend: ATR-channel direction flip with chart-only bullish "
    "and bearish markers. Convert it to a Strategy API source before backtesting "
    "or live trading."
)

# Unit: 0–1 ratio (0.04 = 4% underlying price move; 0.001 = 0.1%; entryPct 1 = 100% capital)
# ===== Configurable params (auto-detected from range metadata) =====
# @param atr_period int 10 ATR Wilder smoothing period range=7:21:1
# @param multiplier float 3.0 ATR band multiplier range=1.5:5.0:0.5

atr_period = int(params.get('atr_period', 10))
multiplier = float(params.get('multiplier', 3.0))

df = df.copy()
high = df['high']
low = df['low']
close = df['close']
prev_close = close.shift(1)

# --- 1) True Range = max(H-L, |H-prevC|, |L-prevC|)
tr = pd.concat([
    high - low,
    (high - prev_close).abs(),
    (low - prev_close).abs(),
], axis=1).max(axis=1)

# --- 2) ATR via Wilder smoothing (RMA); first atr_period-1 bars are NaN
atr = tr.ewm(alpha=1.0 / atr_period, adjust=False, min_periods=atr_period).mean()

# --- 3) Basic upper / lower bands
hl2 = (high + low) / 2.0
upper_basic = hl2 + multiplier * atr
lower_basic = hl2 - multiplier * atr

# --- 4) Final bands + direction (path-dependent loop)
n = len(df)
ub = upper_basic.to_numpy()
lb = lower_basic.to_numpy()
cl = close.to_numpy()

final_upper = np.full(n, np.nan)
final_lower = np.full(n, np.nan)
direction = np.zeros(n, dtype=np.int8)   # 1=long, -1=short, 0=warmup
supertrend = np.full(n, np.nan)

# Wait for Wilder ATR to stabilise before emitting any direction
start_idx = int(atr_period)

for i in range(n):
    if i < start_idx or np.isnan(ub[i]) or np.isnan(lb[i]):
        # Warmup bar: no signal, direction stays 0
        continue

    if i == start_idx or direction[i - 1] == 0:
        # First valid bar: seed direction from close vs band midline
        final_upper[i] = ub[i]
        final_lower[i] = lb[i]
        direction[i] = 1 if cl[i] >= (ub[i] + lb[i]) / 2.0 else -1
        supertrend[i] = final_lower[i] if direction[i] == 1 else final_upper[i]
        continue

    # Upper band may only tighten downward, unless price already broke above it
    if (ub[i] < final_upper[i - 1]) or (cl[i - 1] > final_upper[i - 1]):
        final_upper[i] = ub[i]
    else:
        final_upper[i] = final_upper[i - 1]

    # Lower band may only tighten upward, unless price already broke below it
    if (lb[i] > final_lower[i - 1]) or (cl[i - 1] < final_lower[i - 1]):
        final_lower[i] = lb[i]
    else:
        final_lower[i] = final_lower[i - 1]

    # Direction flip when close breaks the previous final band
    if cl[i] > final_upper[i - 1]:
        direction[i] = 1
    elif cl[i] < final_lower[i - 1]:
        direction[i] = -1
    else:
        direction[i] = direction[i - 1]

    supertrend[i] = final_lower[i] if direction[i] == 1 else final_upper[i]

# --- 5) Edge-triggered chart markers on trend flip
prev_direction = np.concatenate([[0], direction[:-1]])
flip_long = (direction == 1) & (prev_direction == -1)
flip_short = (direction == -1) & (prev_direction == 1)

# --- 6) Two-colour SuperTrend line: green while long, red while short
supertrend_up = [float(v) if (d == 1 and not np.isnan(v)) else None
                 for v, d in zip(supertrend, direction)]
supertrend_dn = [float(v) if (d == -1 and not np.isnan(v)) else None
                 for v, d in zip(supertrend, direction)]

open_long_marks = [df['low'].iloc[i] * 0.995 if bool(flip_long[i]) else None
                   for i in range(n)]
open_short_marks = [df['high'].iloc[i] * 1.005 if bool(flip_short[i]) else None
                    for i in range(n)]

output = {
    'name': my_indicator_name,
    'plots': [
        {'name': 'SuperTrend Up', 'data': supertrend_up, 'color': '#00E676', 'overlay': True},
        {'name': 'SuperTrend Down', 'data': supertrend_dn, 'color': '#FF5252', 'overlay': True},
    ],
    'signals': [
        {'type': 'buy', 'text': 'L', 'data': open_long_marks, 'color': '#00E676'},
        {'type': 'sell', 'text': 'S', 'data': open_short_marks, 'color': '#FF5252'},
    ],
}
'''


_SMA_CODE = r'''# QuantDinger chart indicator: simple moving average
my_indicator_name = "[内置] 简单移动平均线（SMA）"
my_indicator_description = "用收盘价计算可调周期的简单移动平均线，仅用于图表研究。"
# @param period int 20 range=5:200:1
period = int(params.get("period", 20))
df = df.copy()
sma = df["close"].rolling(period, min_periods=period).mean()
output = {
    "name": my_indicator_name,
    "plots": [{"name": "SMA", "data": sma.tolist(), "color": "#29B6F6", "overlay": True}],
    "signals": [],
}
'''


_RSI_CODE = r'''# QuantDinger chart indicator: relative strength index
my_indicator_name = "[内置] 相对强弱指标（RSI）"
my_indicator_description = "识别超买超卖区域的动量指标，仅用于图表研究。"
# @param period int 14 range=5:50:1
period = int(params.get("period", 14))
df = df.copy()
delta = df["close"].diff()
gain = delta.clip(lower=0).rolling(period, min_periods=period).mean()
loss = (-delta.clip(upper=0)).rolling(period, min_periods=period).mean()
rs = gain / loss.replace(0, np.nan)
rsi = (100 - (100 / (1 + rs))).fillna(50)
output = {
    "name": my_indicator_name,
    "plots": [{"name": "RSI", "data": rsi.tolist(), "color": "#AB47BC", "overlay": False}],
    "signals": [],
}
'''


_MACD_CODE = r'''# QuantDinger chart indicator: MACD
my_indicator_name = "[内置] MACD 动量指标"
my_indicator_description = "比较快慢指数移动平均线与信号线的动量变化，仅用于图表研究。"
# @param fast_period int 12 range=5:50:1
# @param slow_period int 26 range=10:100:1
# @param signal_period int 9 range=3:30:1
fast_period = int(params.get("fast_period", 12))
slow_period = int(params.get("slow_period", 26))
signal_period = int(params.get("signal_period", 9))
df = df.copy()
fast = df["close"].ewm(span=fast_period, adjust=False).mean()
slow = df["close"].ewm(span=slow_period, adjust=False).mean()
line = fast - slow
signal = line.ewm(span=signal_period, adjust=False).mean()
histogram = line - signal
output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "MACD", "data": line.tolist(), "color": "#26A69A", "overlay": False},
        {"name": "Signal", "data": signal.tolist(), "color": "#FFCA28", "overlay": False},
        {"name": "Histogram", "data": histogram.tolist(), "color": "#7E57C2", "overlay": False},
    ],
    "signals": [],
}
'''


_BOLLINGER_CODE = r'''# QuantDinger chart indicator: Bollinger Bands
my_indicator_name = "[内置] 布林带"
my_indicator_description = "用移动平均和标准差描述价格波动通道，仅用于图表研究。"
# @param period int 20 range=5:200:1
# @param deviations float 2.0 range=0.5:5.0:0.5
period = int(params.get("period", 20))
deviations = float(params.get("deviations", 2.0))
df = df.copy()
middle = df["close"].rolling(period, min_periods=period).mean()
std = df["close"].rolling(period, min_periods=period).std(ddof=0)
upper = middle + deviations * std
lower = middle - deviations * std
output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Upper", "data": upper.tolist(), "color": "#EF5350", "overlay": True},
        {"name": "Middle", "data": middle.tolist(), "color": "#42A5F5", "overlay": True},
        {"name": "Lower", "data": lower.tolist(), "color": "#66BB6A", "overlay": True},
    ],
    "signals": [],
}
'''


_KDJ_CODE = r'''# QuantDinger chart indicator: KDJ stochastic oscillator
my_indicator_name = "[内置] KDJ 随机指标"
my_indicator_description = "观察收盘价在近期高低区间的位置和动量拐点，仅用于图表研究。"
# @param period int 9 range=5:50:1
period = int(params.get("period", 9))
df = df.copy()
lowest = df["low"].rolling(period, min_periods=period).min()
highest = df["high"].rolling(period, min_periods=period).max()
rsv = ((df["close"] - lowest) / (highest - lowest).replace(0, np.nan) * 100).fillna(50)
k = rsv.ewm(alpha=1 / 3, adjust=False).mean()
d = k.ewm(alpha=1 / 3, adjust=False).mean()
j = 3 * k - 2 * d
output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "K", "data": k.tolist(), "color": "#29B6F6", "overlay": False},
        {"name": "D", "data": d.tolist(), "color": "#FFCA28", "overlay": False},
        {"name": "J", "data": j.tolist(), "color": "#AB47BC", "overlay": False},
    ],
    "signals": [],
}
'''


def _builtin_specs() -> List[Dict[str, str]]:
    """内置指标：name / description / code（与指标 IDE、回测引擎约定一致）。

    现在只保留一个高质量示例 —— 经典 SuperTrend，作为「新手第一份指标」
    的标杆样本：四路信号、契约 v1 注释、可调参数化、严格无未来数据。
    """
    return [
        {
            "name": _BUILTIN_PACK_ANCHOR_NAME,
            "description": (
                "Classic SuperTrend (ATR-channel direction flip): Wilder-smoothed ATR "
                "drives adaptive upper / lower bands; opens on trend flip and closes "
                "on the reverse flip. Tunable params are declared via @param so the "
                "Smart Tuner can sweep them out-of-the-box."
            ),
            "code": _SUPERTREND_CODE,
        },
        {
            "name": "[内置] 简单移动平均线（SMA）",
            "description": "收盘价的简单移动平均线，用于识别趋势方向。",
            "code": _SMA_CODE,
        },
        {
            "name": "[内置] 相对强弱指标（RSI）",
            "description": "动量与超买超卖区域研究指标。",
            "code": _RSI_CODE,
        },
        {
            "name": "[内置] MACD 动量指标",
            "description": "快慢均线与信号线的动量研究指标。",
            "code": _MACD_CODE,
        },
        {
            "name": "[内置] 布林带",
            "description": "移动平均与标准差构成的波动通道指标。",
            "code": _BOLLINGER_CODE,
        },
        {
            "name": "[内置] KDJ 随机指标",
            "description": "观察价格在近期高低区间位置的动量指标。",
            "code": _KDJ_CODE,
        },
    ]


def seed_builtin_indicators_for_new_user(db: Any, user_id: int) -> int:
    """
    注册成功后写入示例指标包。若该用户已有锚点名称指标则跳过（幂等）。
    返回本次插入条数。
    """
    if not user_id:
        return 0
    now = int(time.time())
    cur = db.cursor()
    try:
        cur.execute(
            """
            SELECT name
            FROM qd_indicator_codes
            WHERE user_id = ?
            """,
            (user_id,),
        )
        existing_names = {
            str(row.get("name") or "")
            for row in (cur.fetchall() or [])
            if isinstance(row, dict)
        }

        inserted = 0
        for spec in _builtin_specs():
            if spec["name"] in existing_names:
                continue
            cur.execute(
                """
                INSERT INTO qd_indicator_codes
                  (user_id, is_buy, end_time, name, code, description,
                   publish_to_community, pricing_type, price, preview_image, vip_free, review_status,
                   createtime, updatetime, created_at, updated_at)
                VALUES (?, 0, 1, ?, ?, ?, 0, 'free', 0, '', FALSE, NULL, ?, ?, NOW(), NOW())
                """,
                (
                    user_id,
                    spec["name"],
                    spec["code"],
                    spec["description"],
                    now,
                    now,
                ),
            )
            inserted += 1
        db.commit()
        if inserted:
            logger.info("Seeded %s builtin indicator(s) for new user_id=%s", inserted, user_id)
        return inserted
    except Exception as e:
        logger.warning("seed_builtin_indicators_for_new_user failed user_id=%s: %s", user_id, e)
        try:
            db.rollback()
        except Exception:
            pass
        return 0
    finally:
        try:
            cur.close()
        except Exception:
            pass


def _code_contains_legacy_execution_contract(code: str) -> bool:
    raw = code or ""
    return any(token in raw for token in _LEGACY_EXECUTION_TOKENS)


def upgrade_builtin_indicator_samples() -> int:
    """
    Upgrade persisted official samples to the current chart-indicator contract.

    Older built-in samples contained execution-contract annotations and signal
    column names. This intentionally updates only known official sample names
    when those legacy tokens are still present.
    """
    from app.utils.db import get_db_connection

    specs = _builtin_specs()
    if not specs:
        return 0

    target = specs[0]
    placeholders = ",".join(["?"] * len(_LEGACY_BUILTIN_SAMPLE_NAMES))
    now = int(time.time())
    updated = 0

    with get_db_connection() as db:
        cur = db.cursor()
        try:
            cur.execute(
                f"""
                SELECT id, name, code
                FROM qd_indicator_codes
                WHERE (is_buy IS NULL OR is_buy = 0)
                  AND (publish_to_community IS NULL OR publish_to_community = 0)
                  AND name IN ({placeholders})
                """,
                tuple(_LEGACY_BUILTIN_SAMPLE_NAMES),
            )
            rows = cur.fetchall() or []
            for row in rows:
                row_id = row.get("id") if isinstance(row, dict) else row[0]
                code = row.get("code") if isinstance(row, dict) else row[2]
                if not _code_contains_legacy_execution_contract(code or ""):
                    continue
                cur.execute(
                    """
                    UPDATE qd_indicator_codes
                    SET name = ?, code = ?, description = ?,
                        updatetime = ?, updated_at = NOW()
                    WHERE id = ?
                    """,
                    (
                        target["name"],
                        target["code"],
                        target["description"],
                        now,
                        row_id,
                    ),
                )
                updated += int(cur.rowcount or 0)
            db.commit()
            if updated:
                logger.info("Upgraded %s builtin indicator sample(s) to chart-only contract", updated)
            return updated
        except Exception as e:
            try:
                db.rollback()
            except Exception:
                pass
            logger.warning("upgrade_builtin_indicator_samples failed: %s", e)
            return 0
        finally:
            try:
                cur.close()
            except Exception:
                pass
