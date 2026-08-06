from app.services.live_trading import base


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    text = "{}"
    status_code = 200

    @staticmethod
    def json():
        return {}


def test_explicit_proxy_url_is_passed_to_exchange_rest(monkeypatch):
    captured = {}

    def fake_request(**kwargs):
        captured.update(kwargs)
        return _Response()

    monkeypatch.setenv("PROXY_URL", "http://127.0.0.1:7897")
    monkeypatch.setattr(base.requests, "request", fake_request)
    base.BaseRestClient("https://api-testnet.gateapi.io")._request("GET", "/api/v4/spot/time")

    assert captured["proxies"] == {
        "http": "http://127.0.0.1:7897",
        "https": "http://127.0.0.1:7897",
    }


def test_proxy_is_not_injected_when_explicit_setting_is_absent(monkeypatch):
    captured = {}

    def fake_request(**kwargs):
        captured.update(kwargs)
        return _Response()

    monkeypatch.delenv("PROXY_URL", raising=False)
    monkeypatch.setattr(base.requests, "request", fake_request)
    base.BaseRestClient("https://api-testnet.gateapi.io")._request("GET", "/api/v4/spot/time")

    assert "proxies" not in captured
