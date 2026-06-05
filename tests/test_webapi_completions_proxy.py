from fastapi.testclient import TestClient

from webapi.app import create_app


class _FakeLLMResponse:
    status_code = 200

    def json(self):
        return {
            "id": "chatcmpl_test",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
        }


class _FakeAsyncClient:
    last_post = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, target, *, content, headers):
        type(self).last_post = {
            "target": target,
            "content": content,
            "headers": headers,
        }
        return _FakeLLMResponse()


def test_chat_completions_proxy_uses_configured_local_backend(monkeypatch):
    monkeypatch.setattr(
        "webapi.routes.completions_proxy.load_config",
        lambda: {"model": {"base_url": "http://127.0.0.1:8080/v1"}},
    )
    monkeypatch.setattr(
        "webapi.routes.completions_proxy.httpx.AsyncClient",
        _FakeAsyncClient,
    )
    _FakeAsyncClient.last_post = None

    client = TestClient(create_app())
    response = client.post(
        "/v1/chat/completions",
        json={"model": "local/model", "messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "ok"
    assert _FakeAsyncClient.last_post is not None
    assert _FakeAsyncClient.last_post["target"] == (
        "http://127.0.0.1:8080/v1/chat/completions"
    )
    assert _FakeAsyncClient.last_post["headers"] == {
        "Content-Type": "application/json"
    }
