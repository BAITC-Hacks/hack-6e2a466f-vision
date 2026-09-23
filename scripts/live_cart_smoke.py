"""Live catalog to temporary demo cart, with OpenAI disabled and no site writes."""

import tempfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import main
from backend.cart import DemoCartAdapter
from backend.catalog import CatalogClient
from backend.chat import stock_state
from backend.session import SessionStore


def main_check() -> None:
    catalog = CatalogClient()
    if not catalog.configured:
        raise SystemExit("Live catalog credentials are not configured.")
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "demo-cart.sqlite3"
        session = "live-cart-smoke-session"
        with patch.object(main, "catalog", catalog), \
             patch.object(main, "cart", DemoCartAdapter(database)), \
             patch.object(main, "sessions", SessionStore(database)), \
             patch.object(main.settings, "openai_api_key", ""), \
             patch.object(main.settings, "openai_model", ""):
            client = TestClient(main.app)
            parsed = client.post("/api/requirements/extract", json={
                "session_id": session, "message": "Нужен автомат на 16 А, 1 шт",
            })
            parsed.raise_for_status()
            found = client.post("/api/requirements/search", json={"session_id": session})
            found.raise_for_status()
            result = found.json()
            candidates = [row["product"] for row in result["products"]
                          if stock_state(row["product"])[1] is not None and stock_state(row["product"])[1] >= 1]
            if not candidates:
                raise RuntimeError("No live candidate had a confirmed positive stock; cart was not changed.")
            product_id = str(candidates[0]["id"])
            action = {"session_id": session, "product_id": product_id, "quantity": 1}
            prepared = client.post("/api/cart/prepare", json=action)
            prepared.raise_for_status()
            if client.get(f"/api/cart/{session}").json()["total_items"] != 0:
                raise RuntimeError("Demo cart changed before confirmation.")
            confirmed = client.post("/api/cart/confirm", json=action)
            confirmed.raise_for_status()
            if client.get(f"/api/cart/{session}").json()["total_items"] != 1:
                raise RuntimeError("Confirmed demo cart quantity is incorrect.")
            print(f"Live catalog to temporary demo cart: OK; product ID={product_id}; "
                  f"coverage_complete={result['coverage_complete']}; OpenAI calls=0")


if __name__ == "__main__":
    main_check()
