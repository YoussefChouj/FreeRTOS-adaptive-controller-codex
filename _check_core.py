"""Sanity-check the core.py changes without booting the bridge."""
from ground_station.service.core import GroundStationService

svc = GroundStationService()
print('init OK; freshness_ttl_ns=', svc._slot_freshness_ttl_ns)
svc.set_slot_freshness_ttl(15.0)
print('after set:', svc._slot_freshness_ttl_ns)
print('_resolve_tag_slot("a"):', GroundStationService._resolve_tag_slot('a'))
print('_resolve_tag_slot("s0"):', GroundStationService._resolve_tag_slot('s0'))
print('_resolve_tag_slot("s3"):', GroundStationService._resolve_tag_slot('s3'))
print('_resolve_tag_slot("data"):', GroundStationService._resolve_tag_slot('data'))
print('_resolve_tag_slot("foo"):', GroundStationService._resolve_tag_slot('foo'))
