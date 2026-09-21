"""Offline /api/symbols verification: prints verbatim wire responses."""
import http.client
import json

from ground_station.livewatch.stream import StreamRange, StreamSchema
from ground_station.service.api import ApiServer
from ground_station.service.core import GroundStationService
from ground_station.service.storage import SessionStore


def _service():
    schema = StreamSchema(1, 1, 4,
                          (StreamRange(0x20000000, 4, 1, "altitude", "f"),), 0)
    return GroundStationService(store=SessionStore(), schemas=[schema], source="sim")


def _raw(port, path):
    conn = http.client.HTTPConnection("127.0.0.1", port)
    conn.connect()
    conn.request("GET", path)
    resp = conn.getresponse()
    body = resp.read().decode()
    conn.close()
    return resp.status, body


service = _service()
service.start()
api = ApiServer(service)
api.start()
try:
    port = api.address[1]
    for label, path in (
        ("PREFIX QUERY", "/api/symbols?prefix=Gyro"),
        ("UNFILTERED (default limit)", "/api/symbols"),
        ("SIZE BOUND limit=5", "/api/symbols?limit=5"),
        ("OVER-CAP limit=999999", "/api/symbols?limit=999999"),
    ):
        status, body = _raw(port, path)
        print("=== %s ===" % label)
        print("GET " + path)
        print("HTTP " + str(status))
        print(body)
        print()
finally:
    api.stop()
