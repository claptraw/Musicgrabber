"""Connection pool checkout behaviour, mostly guarding against the cold-start stall."""

import os
import queue
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db


def _fresh_pool(monkeypatch, tmp_path, size=8):
    """Point the pool at a throwaway database and empty it, as at process start."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "pool.db")
    monkeypatch.setattr(db, "_DB_POOL_SIZE", size)
    monkeypatch.setattr(db, "_db_pool", queue.LifoQueue(maxsize=size))
    monkeypatch.setattr(db, "_db_pool_created", 0)
    monkeypatch.setattr(db, "_db_pool_lock", threading.Lock())


def test_first_connection_on_a_cold_pool_is_immediate(monkeypatch, tmp_path):
    """The original sin: an empty pool made the first query wait out a 15s timeout."""
    _fresh_pool(monkeypatch, tmp_path)
    start = time.perf_counter()
    with db.db_conn() as conn:
        conn.execute("SELECT 1")
    assert time.perf_counter() - start < 1.0


def test_pool_grows_to_its_ceiling_without_waiting(monkeypatch, tmp_path):
    """Every connection up to the ceiling is created on demand, none of them blocking."""
    _fresh_pool(monkeypatch, tmp_path, size=4)
    start = time.perf_counter()
    held = [db._get_pooled_conn() for _ in range(4)]
    elapsed = time.perf_counter() - start
    assert len({id(c) for c in held}) == 4
    assert elapsed < 1.0
    assert db._db_pool_created == 4
    for conn in held:
        db._return_pooled_conn(conn)


def test_returned_connections_are_reused_rather_than_multiplying(monkeypatch, tmp_path):
    """Checkout/return should not keep minting connections; that is the whole point."""
    _fresh_pool(monkeypatch, tmp_path, size=4)
    for _ in range(10):
        with db.db_conn() as conn:
            conn.execute("SELECT 1")
    assert db._db_pool_created == 1


def test_a_fully_loaned_out_pool_still_blocks(monkeypatch, tmp_path):
    """Past the ceiling we deliberately wait, so sqlite is not flooded with writers."""
    _fresh_pool(monkeypatch, tmp_path, size=2)
    held = [db._get_pooled_conn() for _ in range(2)]
    waited = threading.Event()

    def borrow():
        conn = db._get_pooled_conn()
        waited.set()
        db._return_pooled_conn(conn)

    t = threading.Thread(target=borrow, daemon=True)
    t.start()
    assert not waited.wait(timeout=0.3)  # nothing spare, so it is queueing patiently
    db._return_pooled_conn(held.pop())
    assert waited.wait(timeout=5)  # handed one back, waiter wakes up
    t.join(timeout=5)
    for conn in held:
        db._return_pooled_conn(conn)


def test_concurrent_cold_start_never_exceeds_the_ceiling(monkeypatch, tmp_path):
    """Twelve threads off the blocks at once must not create thirteen connections."""
    _fresh_pool(monkeypatch, tmp_path, size=4)
    go = threading.Event()
    errors = []

    def worker():
        go.wait()
        try:
            with db.db_conn() as conn:
                conn.execute("SELECT 1")
        except Exception as e:  # a locked or missing DB would surface here
            errors.append(e)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(12)]
    for t in threads:
        t.start()
    go.set()
    for t in threads:
        t.join(timeout=30)

    assert not errors
    assert db._db_pool_created <= 4
