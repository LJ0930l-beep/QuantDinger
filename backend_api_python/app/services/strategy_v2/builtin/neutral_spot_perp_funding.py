"""NEUTRAL-01: Spot-Perpetual Funding/Basis Neutral Strategy (Paper Only, Phase 3)

Source: hummingbot/hummingbot (Apache-2.0 — PMM/arbitrage ideas) +
        nateemma/strategies (GPL-3.0 — funding ideas studied, NOT copied)
License: Independent QuantDinger implementation.

Specification per《QuantDinger GitHub 策略引入与落地实施方案》v1.0 §5.6:
  Market:      Same-exchange SPOT + PERPETUAL, Delta Neutral
  Timeframe:   Funding cycle level; monitor 1m-15m

⚠️ CRITICAL: Phase 3 — Paper/Shadow ONLY.
  Gate TestNet auto-write MUST remain OFF (GATE_TESTNET_WRITE_ENABLED=0).
  Live MUST remain OFF (AGENT_LIVE_TRADING_ENABLED=0).
  This is a SKELETON — hedge state machine exists (hedge_candidate_contracts.py)
  but the full execution path has NOT been implemented yet.

Strategy Goal:
  Capture funding/basis convergence without directional exposure.
  Positive funding: buy SPOT + short PERP.
  Both legs form one hedge_group_id; executed atomically or compensated.

Entry Logic (NOT auto-routed to TestNet in v1):
  - Expected funding/basis return covers ALL costs (fees, slippage, holding)
  - HedgeCandidate generated with both legs, delta tolerance, margin buffer
  - Legs submitted together via HedgeAdmission (not yet implemented)
  - Any leg failure → compensation/undo state machine

Exit Logic:
  - Funding yield drops below exit threshold
  - Basis reaches convergence target
  - Margin/liquidity/borrow/account health deteriorates
  - Single-leg anomaly → emergency hedge/reduce flow

Architecture Note:
  This strategy file defines an OUTLINE only. The actual execution requires
  the HedgeAdmissionGate and HedgedPositionStateMachine which are defined
  in app/domain/hedge_candidate_contracts.py but have NOT been wired into
  the trading executor yet (blocked on P3 infrastructure).

  The v1 strategy code below serves as a specification-enforcing placeholder.
  It will NOT auto-execute — it produces HedgeCandidate dataclass instances
  (via domain contracts) but has no path to TestNet/Live submission.
"""

# This strategy runs in the Strategy V2 sandbox but produces domain
# contract outputs rather than context.order() calls.
# The execution layer MUST detect NEUTRAL_01 type and route through
# HedgeAdmission rather than standard order pipeline.

STRATEGY_CODE = '''
"""NEUTRAL-01: Spot-Perpetual Funding/Basis Neutral — Phase 3 Skeleton.

This is a SPECIFICATION-ONLY placeholder.  No orders are emitted.
The strategy observes conditions and would generate HedgeCandidate
evidence when the infrastructure is ready.
"""

def initialize(context):
    context.set_universe(["Crypto:BTC/USDT@spot"])
    context.set_warmup(200)
    context.hedge_group_id = ""
    context.leg1_open = False
    context.leg2_open = False
    context.state = "IDLE"  # Matches HedgeState enum

def handle_data(context):
    closes = [b.close for b in context.bars]
    if len(closes) < 200:
        return

    symbol = context.instruments[0]
    price = closes[-1]

    # ── Phase 3: State monitoring only ──────────────────────
    # When HedgeAdmission is wired:
    #   1. Detect funding/basis opportunity
    #   2. Build HedgeCandidate (via domain contracts)
    #   3. Submit to HedgeAdmissionGate
    #   4. HedgeStateMachine tracks leg lifecycle
    #
    # For now: NO_ACTION on every bar.
    # This strategy produces zero orders in v1.

    context.state = "IDLE"

    # Diagnostic: conditions that WOULD trigger in full implementation
    would_trigger = False
    # (Placeholder for funding/basis detection logic)

    if would_trigger:
        # Would generate HedgeCandidate here
        pass
'''


MARKET_SUITABLE = "crypto_spot, crypto_swap"  # Both legs needed
SUGGESTED_TIMEFRAME = "1m, 5m, 15m"
RISK_LEVEL = "neutral"

# ── NEUTRAL-01 Specific Metadata ─────────────────────────────

NEUTRAL_PHASE = 3  # Phase 3: skeleton only, no auto-execution

HEDGE_STATE_MACHINE_DEFINITION = {
    "states": ["IDLE", "LEG1_REQUESTED", "LEG1_OPEN", "LEG2_REQUESTED",
               "FULLY_HEDGED", "LEG1_CLOSING", "LEG2_CLOSING",
               "EMERGENCY_UNWIND", "FAILED"],
    "transitions": {
        "IDLE": ["LEG1_REQUESTED"],
        "LEG1_REQUESTED": ["LEG1_OPEN", "FAILED"],
        "LEG1_OPEN": ["LEG2_REQUESTED", "LEG1_CLOSING", "EMERGENCY_UNWIND"],
        "LEG2_REQUESTED": ["FULLY_HEDGED", "FAILED", "EMERGENCY_UNWIND"],
        "FULLY_HEDGED": ["LEG1_CLOSING", "LEG2_CLOSING", "EMERGENCY_UNWIND"],
        "LEG1_CLOSING": ["IDLE", "LEG1_OPEN", "EMERGENCY_UNWIND"],
        "LEG2_CLOSING": ["LEG1_OPEN", "EMERGENCY_UNWIND"],
        "EMERGENCY_UNWIND": ["IDLE", "FAILED"],
        "FAILED": [],
    },
}

STRATEGY_SOURCE = {
    "repo": "hummingbot/hummingbot (Apache-2.0) + nateemma/strategies (GPL-3.0)",
    "license": "Independent reimplementation — no code copied from either source",
    "files_referenced": [
        "hummingbot/strategy/spot_perpetual_arbitrage/ (arbitrage state machine concept)",
        "nateemma/strategies/Framework/ (funding signal concept)",
    ],
    "what_was_borrowed": [
        "Delta-neutral concept: buy spot + short perp (public domain arbitrage)",
        "Two-leg state machine with failure compensation (independent implementation)",
    ],
    "what_is_original": [
        "Entire HedgeCandidate contract (app/domain/hedge_candidate_contracts.py)",
        "9-state HedgeStateMachine with enforced transitions",
        "Failure compensation plan (HedgeFailureCompensation)",
        "Phase-gated execution (Phase 3: Paper only, no auto-TestNet)",
        "All code independently written",
    ],
    "access_date": "2026-08-06",
    "phase_note": (
        "Phase 3 skeleton only.  Full funding/basis detection, HedgeAdmission "
        "wiring, and Paper execution deferred to P3 infrastructure (HedgeAdmissionGate + "
        "HedgedPositionStateMachine in trading executor).  This file exists to "
        "hold the specification and prevent premature execution."
    ),
}
