# Sirve `/x` como `/x/` sin el 307 de FastAPI: iOS quita `Authorization` al seguir redirecciones.

from starlette.routing import Match, Router
from starlette.types import ASGIApp, Receive, Scope, Send


class TrailingSlashRewriteMiddleware:
    def __init__(self, app: ASGIApp, router: Router) -> None:
        self.app = app
        self.router = router

    def _matches(self, scope: Scope) -> bool:
        return any(
            route.matches(scope)[0] == Match.FULL for route in self.router.routes
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            path: str = scope["path"]
            if path != "/" and not path.endswith("/") and not self._matches(scope):
                with_slash = {
                    **scope,
                    "path": path + "/",
                    "raw_path": scope.get("raw_path", path.encode()) + b"/",
                }
                if self._matches(with_slash):
                    scope = with_slash
        await self.app(scope, receive, send)
