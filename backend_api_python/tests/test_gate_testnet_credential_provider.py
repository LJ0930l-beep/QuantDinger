"""Safety tests for the explicit local Gate TestNet credential source."""

from __future__ import annotations

import unittest

from app.services.gate_testnet_credential_provider import (
    GateTestnetCredentialProviderError,
    credential_from_environment,
)


class GateTestnetCredentialProviderTests(unittest.TestCase):
    def test_requires_explicit_environment_values(self):
        with self.assertRaises(GateTestnetCredentialProviderError):
            credential_from_environment(environ={})

    def test_refuses_live_flag_and_never_reveals_values(self):
        values = {
            "GATE_TESTNET_API_KEY": "example-key",
            "GATE_TESTNET_API_SECRET": "example-secret",
            "AGENT_LIVE_TRADING_ENABLED": "true",
        }
        with self.assertRaises(GateTestnetCredentialProviderError):
            credential_from_environment(environ=values)
        values["AGENT_LIVE_TRADING_ENABLED"] = "false"
        credential = credential_from_environment(environ=values)
        self.assertNotIn("example-key", repr(credential))
        self.assertNotIn("example-secret", repr(credential))
        self.assertEqual(credential.environment.value, "testnet")


if __name__ == "__main__":
    unittest.main()
