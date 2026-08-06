#!/usr/bin/env python
"""QuantDinger Soak Test Runner — continuous end-to-end verification.

Runs on Gate TestNet with PAPER execution mode. Monitors the full pipeline:
authority projection → risk facts → pipeline health → admit → reconciliation.

Usage: python scripts/soak_test.py [--cycles N] [--interval S]
"""

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

import requests

BASE = "http://127.0.0.1:5000"
CREDENTIAL_ID = 3896
INSTRUMENT = "BTC_USDT"
MARKET_TYPE = "spot"
ACCOUNT_SCOPE = "spot"

class SoakRunner:
    def __init__(self, cycles: int = 10, interval: float = 5.0):
        self.cycles = cycles
        self.interval = interval
        self.token = None
        self.results: List[Dict[str, Any]] = []

    def login(self):
        resp = requests.post(
            f"{BASE}/api/auth/login",
            json={"username": "testadmin", "password": "testpass123"},
            timeout=10,
        )
        resp.raise_for_status()
        self.token = resp.json()["data"]["token"]
        print(f"[AUTH] logged in")

    def _post(self, path: str, payload: dict, timeout: int = 30) -> dict:
        resp = requests.post(
            f"{BASE}{path}",
            json=payload,
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=timeout,
        )
        return resp.json()

    def authority_project(self) -> bool:
        r = self._post(
            "/api/quant/runtime-entry/authority/project",
            {"credential_id": CREDENTIAL_ID, "account_scope": ACCOUNT_SCOPE,
             "market_type": MARKET_TYPE, "instrument_id": INSTRUMENT},
        )
        ok = r.get("status") == "PROJECTED"
        disp = r.get("dispositions", {})
        print(f"  [authority] {r['status']} spot={disp.get('spot','?')}")
        return ok

    def pipeline_run(self) -> bool:
        r = self._post(
            "/api/quant/runtime-entry/pipeline/run",
            {"credential_id": CREDENTIAL_ID, "account_scope": ACCOUNT_SCOPE,
             "market_type": MARKET_TYPE, "instrument_id": INSTRUMENT},
        )
        ok = r.get("status") == "PIPELINED"
        cp = r.get("checkpoint", {})
        print(f"  [pipeline] {r['status']} health={cp.get('health','?')} disc={cp.get('discrepancy_count','?')}")
        return ok

    def admit_paper(self, action: str, side: str) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        key = f"soak-{uuid.uuid4().hex[:12]}"
        r = self._post(
            "/api/quant/entry/admit",
            {
                "source": "REST", "mode": "PAPER",
                "credential_id": CREDENTIAL_ID,
                "instrument_id": INSTRUMENT, "market_type": MARKET_TYPE,
                "action": action, "side": side,
                "quantity": "0.0001", "quantity_semantics": "ABSOLUTE",
                "execution_kind": "LIMIT", "limit_price": "100.0",
                "position_side": "NET", "reduce_only": False, "close_all": False,
                "idempotency_key": key, "correlation_id": f"soak-{key[:8]}",
                "occurred_at": now,
            },
        )
        print(f"  [admit] {r.get('status','?')} risk={r.get('risk_decision_status','?')} "
              f"action={action} side={side}")
        return r

    def run_cycle(self, cycle: int):
        print(f"\n{'='*50}")
        print(f"Cycle {cycle}/{self.cycles}")
        print(f"{'='*50}")

        # Step 1: Authority projection
        auth_ok = self.authority_project()

        # Step 2: Pipeline health
        pipe_ok = self.pipeline_run()

        # Step 3: Admit OPEN BUY
        r1 = self.admit_paper("OPEN", "BUY")

        # Step 4: Pipeline health after order
        self.pipeline_run()

        # Step 5: Admit CLOSE SELL
        r2 = self.admit_paper("CLOSE", "SELL")

        # Step 6: Final pipeline health
        self.pipeline_run()

        cycle_result = {
            "cycle": cycle,
            "authority_ok": auth_ok,
            "pipeline_ok": pipe_ok,
            "open_status": r1.get("status"),
            "close_status": r2.get("status"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self.results.append(cycle_result)

        return all([
            auth_ok, pipe_ok,
            r1.get("status") == "CREATED",
            r2.get("status") == "CREATED",
        ])

    def run(self):
        print("QuantDinger Soak Test")
        print(f"Cycles: {self.cycles}, Interval: {self.interval}s")
        print(f"Credential: {CREDENTIAL_ID}, Instrument: {INSTRUMENT}")

        self.login()

        passed = 0
        failed = 0
        for cycle in range(1, self.cycles + 1):
            try:
                ok = self.run_cycle(cycle)
                if ok:
                    passed += 1
                    print(f"  >>> Cycle {cycle} PASSED")
                else:
                    failed += 1
                    print(f"  >>> Cycle {cycle} FAILED")
            except Exception as e:
                failed += 1
                print(f"  >>> Cycle {cycle} ERROR: {e}")

            if cycle < self.cycles:
                time.sleep(self.interval)

        # Summary
        print(f"\n{'='*50}")
        print(f"SOAK COMPLETE")
        print(f"  Passed: {passed}/{self.cycles}")
        print(f"  Failed: {failed}/{self.cycles}")
        print(f"  Results: {json.dumps(self.results, indent=2)}")

        return failed == 0


def main():
    parser = argparse.ArgumentParser(description="QuantDinger Soak Test")
    parser.add_argument("--cycles", type=int, default=5, help="Number of test cycles")
    parser.add_argument("--interval", type=float, default=3.0, help="Seconds between cycles")
    args = parser.parse_args()

    runner = SoakRunner(cycles=args.cycles, interval=args.interval)
    success = runner.run()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
