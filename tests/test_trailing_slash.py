from fastapi import APIRouter, FastAPI, Header
from fastapi.testclient import TestClient

from src.main import app as main_app
from src.trailing_slash import TrailingSlashRewriteMiddleware


def _app() -> FastAPI:
    app = FastAPI()
    router = APIRouter(prefix="/items")

    @router.get("/")
    def list_items(authorization: str | None = Header(default=None)):
        return {"auth": authorization}

    @router.get("/{item_id}")
    def get_item(item_id: int):
        return {"id": item_id}

    app.include_router(router)
    app.add_middleware(TrailingSlashRewriteMiddleware, router=app.router)
    return app


client = TestClient(_app())


def test_collection_without_slash_is_served_without_redirect():
    response = client.get(
        "/items", headers={"Authorization": "Bearer t"}, follow_redirects=False
    )
    assert response.status_code == 200
    assert response.json() == {"auth": "Bearer t"}


def test_collection_with_slash_still_works():
    assert client.get("/items/").status_code == 200


def test_item_route_is_not_rewritten():
    response = client.get("/items/7", follow_redirects=False)
    assert response.status_code == 200
    assert response.json() == {"id": 7}


def test_unknown_path_is_still_404():
    assert client.get("/nada", follow_redirects=False).status_code == 404


def test_main_app_does_not_redirect_reservations():
    response = TestClient(main_app).get("/reservations", follow_redirects=False)
    assert response.status_code != 307
