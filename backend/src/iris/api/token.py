"""The per-launch API token, set by the desktop app (on stdin: ``iris/launch_secrets.py``; also
read from ``IRIS_API_TOKEN``).

The backend listens on 127.0.0.1 with the user's OpenAI key loaded, so any local process (or a web
page making requests to localhost) could otherwise use it. The desktop app makes a random token at
each launch and its ``app://`` handler adds it to every request it forwards; the page itself never
sees it. When the token is set, every HTTP request must carry it in ``X-Iris-Token``, including
``/api/health`` (the app's health check sends it too) and the OpenAPI docs. When it is unset
(``make dev``, the CLI, tests) nothing changes.
"""

import hmac
import json

from starlette.types import ASGIApp, Receive, Scope, Send

TOKEN_HEADER = b"x-iris-token"


class TokenMiddleware:
    """Refuses (401) any request whose ``X-Iris-Token`` isn't ``token``. Pure ASGI, so streamed
    request and response bodies (uploads, live chunks, server-sent events) pass through as is."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        if not token:
            raise ValueError("TokenMiddleware needs a non-empty token")
        self.app = app
        self.token = token.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):  # lifespan
            return await self.app(scope, receive, send)
        sent = [value for name, value in scope["headers"] if name == TOKEN_HEADER]
        # Exactly one header, compared in constant time.
        if len(sent) == 1 and hmac.compare_digest(sent[0], self.token):
            return await self.app(scope, receive, send)
        if scope["type"] == "websocket":
            return await send({"type": "websocket.close", "code": 1008})
        body = json.dumps({"detail": "missing or wrong API token"}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
