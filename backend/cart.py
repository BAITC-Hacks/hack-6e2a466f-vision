"""Session-scoped demonstration cart. No ekt.kz write endpoint is called."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Protocol


class CartAdapter(Protocol):
    def pending(self, session_id: str) -> dict[str, Any] | None: ...
    def prepare(self, session_id: str, product_id: str, quantity: int | None) -> None: ...
    def set_quantity(self, session_id: str, quantity: int) -> None: ...
    def cancel(self, session_id: str) -> None: ...
    def add_confirmed(self, session_id: str, product: dict[str, Any], quantity: int, stock: int) -> dict[str, Any]: ...
    def change_quantity(self, session_id: str, product: dict[str, Any], quantity: int, stock: int) -> dict[str, Any]: ...
    def remove_item(self, session_id: str, product_id: str) -> dict[str, Any]: ...
    def was_confirmed(self, session_id: str, product_id: str, quantity: int) -> bool: ...
    def contents(self, session_id: str) -> dict[str, Any]: ...


class DemoCartAdapter:
    def __init__(self, database: str | Path = Path(__file__).with_name("demo_cart.sqlite3")) -> None:
        self.database = str(database)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS cart_items (
                session_id TEXT NOT NULL, product_id TEXT NOT NULL,
                product_json TEXT NOT NULL, quantity INTEGER NOT NULL CHECK(quantity > 0),
                PRIMARY KEY (session_id, product_id))""")
            db.execute("""CREATE TABLE IF NOT EXISTS cart_pending (
                session_id TEXT PRIMARY KEY, product_id TEXT NOT NULL, quantity INTEGER)""")
            db.execute("""CREATE TABLE IF NOT EXISTS cart_confirmed (
                session_id TEXT PRIMARY KEY, product_id TEXT NOT NULL, quantity INTEGER NOT NULL)""")

    def was_confirmed(self, session_id: str, product_id: str, quantity: int) -> bool:
        with self._connect() as db:
            row = db.execute("SELECT product_id, quantity FROM cart_confirmed WHERE session_id=?", (session_id,)).fetchone()
        return bool(row and row["product_id"] == product_id and row["quantity"] == quantity)

    def pending(self, session_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT product_id, quantity FROM cart_pending WHERE session_id=?", (session_id,)).fetchone()
        return dict(row) if row else None

    def prepare(self, session_id: str, product_id: str, quantity: int | None) -> None:
        with self._connect() as db:
            db.execute("""INSERT INTO cart_pending(session_id, product_id, quantity) VALUES(?,?,?)
                ON CONFLICT(session_id) DO UPDATE SET product_id=excluded.product_id, quantity=excluded.quantity""",
                (session_id, product_id, quantity))

    def set_quantity(self, session_id: str, quantity: int) -> None:
        with self._connect() as db:
            db.execute("UPDATE cart_pending SET quantity=? WHERE session_id=?", (quantity, session_id))

    def cancel(self, session_id: str) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM cart_pending WHERE session_id=?", (session_id,))

    def add_confirmed(self, session_id: str, product: dict[str, Any], quantity: int, stock: int) -> dict[str, Any]:
        if quantity <= 0:
            raise ValueError("Количество должно быть положительным.")
        product_id = str(product.get("id") or "")
        if not product_id:
            raise ValueError("У товара отсутствует идентификатор.")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            approval = db.execute("SELECT product_id, quantity FROM cart_pending WHERE session_id=?", (session_id,)).fetchone()
            if approval is None:
                previous = db.execute("SELECT product_id, quantity FROM cart_confirmed WHERE session_id=?", (session_id,)).fetchone()
                if previous and previous["product_id"] == product_id and previous["quantity"] == quantity:
                    db.commit()
                    return {**self.contents(session_id), "already_confirmed": True}
                raise ValueError("Нет ожидающего подтверждения для этого товара и количества.")
            if approval["product_id"] != product_id or approval["quantity"] != quantity:
                db.rollback()
                raise ValueError("Запрос не совпадает с ожидающим подтверждением.")
            row = db.execute("SELECT quantity FROM cart_items WHERE session_id=? AND product_id=?", (session_id, product_id)).fetchone()
            current = row["quantity"] if row else 0
            if quantity + current > stock:
                db.rollback()
                raise ValueError(f"В корзине уже {current}; доступный остаток — {stock}.")
            db.execute("""INSERT INTO cart_items(session_id, product_id, product_json, quantity) VALUES(?,?,?,?)
                ON CONFLICT(session_id, product_id) DO UPDATE SET
                  product_json=excluded.product_json, quantity=cart_items.quantity + excluded.quantity""",
                (session_id, product_id, json.dumps(product, ensure_ascii=False), quantity))
            db.execute("DELETE FROM cart_pending WHERE session_id=?", (session_id,))
            db.execute("""INSERT INTO cart_confirmed(session_id, product_id, quantity) VALUES(?,?,?)
                ON CONFLICT(session_id) DO UPDATE SET product_id=excluded.product_id, quantity=excluded.quantity""",
                (session_id, product_id, quantity))
            db.commit()
        return self.contents(session_id)

    def contents(self, session_id: str) -> dict[str, Any]:
        with self._connect() as db:
            rows = db.execute("SELECT product_json, quantity FROM cart_items WHERE session_id=? ORDER BY product_id", (session_id,)).fetchall()
        items = [{"product": json.loads(row["product_json"]), "quantity": row["quantity"]} for row in rows]
        return {"mode": "demo", "items": items, "total_items": sum(row["quantity"] for row in rows)}

    def change_quantity(self, session_id: str, product: dict[str, Any], quantity: int, stock: int) -> dict[str, Any]:
        if quantity <= 0 or quantity > stock:
            raise ValueError(f"Количество должно быть от 1 до подтверждённого остатка {stock}.")
        product_id = str(product.get("id") or "")
        if not product_id:
            raise ValueError("У товара отсутствует идентификатор.")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT 1 FROM cart_items WHERE session_id=? AND product_id=?", (session_id, product_id)).fetchone()
            if row is None:
                raise ValueError("Товар не найден в демонстрационной корзине.")
            db.execute("UPDATE cart_items SET product_json=?, quantity=? WHERE session_id=? AND product_id=?",
                       (json.dumps(product, ensure_ascii=False), quantity, session_id, product_id))
        return self.contents(session_id)

    def remove_item(self, session_id: str, product_id: str) -> dict[str, Any]:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM cart_items WHERE session_id=? AND product_id=?", (session_id, product_id))
        return self.contents(session_id)
