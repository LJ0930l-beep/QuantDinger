"""Create a sanitized, non-live deployment/rollback evidence artifact.

The command only hashes/records operator-supplied release facts. It never
deploys, reads credentials, contacts a venue, or enables live trading.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.domain.deployment_readiness_contracts import (
    DEPLOYMENT_READINESS_CONTRACT_VERSION,
    DeploymentEnvironment,
    DeploymentReleaseProfile,
)
from app.domain.production_readiness_contracts import ProductionReadinessStatus


def build_artifact(args: argparse.Namespace) -> dict:
    profile = DeploymentReleaseProfile(
        release_id=args.release_id,
        environment=DeploymentEnvironment(args.environment),
        artifact_digest=args.artifact_digest,
        schema_fingerprint=args.schema_fingerprint,
        config_fingerprint=args.config_fingerprint,
        rollback_release_id=args.rollback_release_id,
        rollback_verified=args.rollback_verified,
        live_enabled=False,
    )
    return {
        "contract_version": DEPLOYMENT_READINESS_CONTRACT_VERSION,
        "profile": profile.to_public_dict(),
        "production_status": ProductionReadinessStatus(args.production_status).value,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--environment", choices=[item.value for item in DeploymentEnvironment], required=True)
    parser.add_argument("--artifact-digest", required=True)
    parser.add_argument("--schema-fingerprint", required=True)
    parser.add_argument("--config-fingerprint", required=True)
    parser.add_argument("--rollback-release-id", required=True)
    parser.add_argument("--production-status", choices=[item.value for item in ProductionReadinessStatus], required=True)
    parser.add_argument("--rollback-verified", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output
    if not output.is_absolute():
        parser.error("--output must be an absolute path")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(build_artifact(args), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
