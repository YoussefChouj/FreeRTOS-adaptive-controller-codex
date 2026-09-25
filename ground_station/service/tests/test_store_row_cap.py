"""In-memory SessionStore must keep a bounded window of live rows."""
from ground_station.service.storage import MEMORY_MAX_ROWS, SessionStore


def _count(store, table):
    return store._db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_memory_store_caps_telemetry_and_raw_frames():
    store = SessionStore(max_rows=100)
    sid = store.start_session("s")
    for i in range(1000):
        store.append_telemetry(sid, 0, i, {"x": i})
        store.append_raw_frame(sid, "rx", b"\x00" * 8)
    assert _count(store, "telemetry") <= 200
    assert _count(store, "raw_frames") <= 200
    newest = list(store.iter_records(sid))
    assert any(r.get("sequence") == 999 for r in newest if isinstance(r, dict))


def test_default_memory_store_is_capped_and_file_store_is_not(tmp_path):
    assert SessionStore().max_rows == MEMORY_MAX_ROWS
    assert SessionStore(tmp_path / "s.db").max_rows is None
