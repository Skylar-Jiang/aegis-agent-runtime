"""Bound Core request bytes before JSON parsing, including chunked uploads."""

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ra_agent.audit.integrity import MAX_AUDIT_BUNDLE_BYTES


class CoreBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, *, max_write_bytes: int) -> None:
        self.app = app
        # A byte of ASCII content can occupy six bytes when JSON-escaped.
        # The adapter still enforces max_write_bytes on the decoded content.
        self.request_limit = max_write_bytes * 6 + 64 * 1024

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/v1/"):
            await self.app(scope, receive, send)
            return
        limit = (
            MAX_AUDIT_BUNDLE_BYTES + 64 * 1024
            if scope["path"] == "/api/v1/audit/verify"
            else self.request_limit
        )
        headers = dict(scope.get("headers", []))
        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            declared = limit + 1
        rejection = JSONResponse(
            {"detail": {"code": "INPUT_TOO_LARGE", "message": "Core request body exceeds limit"}},
            status_code=413,
        )
        if declared < 0 or declared > limit:
            await rejection(scope, receive, send)
            return
        chunks = bytearray()
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > limit:
                await rejection(scope, receive, send)
                return
            chunks.extend(chunk)
            if not message.get("more_body", False):
                break
        body = bytes(chunks)
        delivered = False

        async def bounded_receive() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, bounded_receive, send)
