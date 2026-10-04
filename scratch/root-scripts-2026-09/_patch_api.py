"""Patch api.py to add full request metadata to diagnostics."""
import re

path = r'C:\Users\Acer\Desktop\UAV_lab\FreeRTOS-adaptive-controller-codex\ground_station\service\api.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# Find and replace the request_states section in the diagnostics bundle
old = '            for rid, state in request_states.items():\n                transport["request_states"][str(rid)] = {\n                    "state": state,\n                    "slot": request_to_slot.get(rid),\n                }\n            for slot_key, ranges in pending.items():'

new = '            for rid, state in request_states.items():\n                meta = getattr(bridge, "_request_metadata", {}).get(rid, {})\n                transport["request_states"][str(rid)] = {\n                    "state": state,\n                    "slot": request_to_slot.get(rid),\n                    # WP1 full request metadata\n                    "created_ns": meta.get("created_ns"),\n                    "sent_ns": meta.get("sent_ns"),\n                    "response_ns": meta.get("response_ns"),\n                    "timeout_ns": meta.get("timeout_ns"),\n                    "retry_count": meta.get("retry_count", 0),\n                    "failure_reason": meta.get("failure_reason"),\n                    "batch_index": meta.get("batch_index"),\n                    "total_batches": meta.get("total_batches"),\n                    "range_count": meta.get("range_count"),\n                    "divider": meta.get("divider"),\n                    "transport": meta.get("transport"),\n                }\n            for slot_key, ranges in pending.items():'

if old in content:
    content = content.replace(old, new)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print('SUCCESS: diagnostics bundle updated')
else:
    print('NOT FOUND in diagnostics bundle section')

# Also update /api/view-model section
old2 = '''                request_states = {}
                if service.bridge is not None:
                    for rid, state in getattr(service.bridge, "_request_states", {}).items():
                        request_states[str(rid)] = {
                            "state": state,
                            "slot": getattr(service.bridge, "_request_to_slot", {}).get(rid),
                        }'''

new2 = '''                request_states = {}
                if service.bridge is not None:
                    for rid, state in getattr(service.bridge, "_request_states", {}).items():
                        meta = getattr(service.bridge, "_request_metadata", {}).get(rid, {})
                        request_states[str(rid)] = {
                            "state": state,
                            "slot": getattr(service.bridge, "_request_to_slot", {}).get(rid),
                            # WP1 full request metadata
                            "created_ns": meta.get("created_ns"),
                            "sent_ns": meta.get("sent_ns"),
                            "response_ns": meta.get("response_ns"),
                            "retry_count": meta.get("retry_count", 0),
                            "failure_reason": meta.get("failure_reason"),
                            "batch_index": meta.get("batch_index"),
                            "total_batches": meta.get("total_batches"),
                            "range_count": meta.get("range_count"),
                            "divider": meta.get("divider"),
                        }'''

if old2 in content:
    content = content.replace(old2, new2)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print('SUCCESS: /api/view-model updated')
else:
    print('NOT FOUND in /api/view-model section')
