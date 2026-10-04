"""Patch wifi_bridge.py: fix WP1 out-of-order guard to accept no-pending-request cases."""
with open('ground_station/comm/wifi_bridge.py', 'rb') as f:
    content = f.read()

old = (
    b'            # matching 0x08 arrives. So at any point, we are waiting for exactly\r\n'
    b'            # one batch_index, and any other 0x08 is stale/duplicate/out-of-order.\r\n'
    b'            expected = self._next_expected_batch.get(slot)\r\n'
    b'            matched_request_id = None\r\n'
    b'            matched_batch_index = None\r\n'
    b'            with self._stream_lock:\r\n'
    b'                for rid, req_slot in list(self._request_to_slot.items()):\r\n'
    b'                    if req_slot == slot:\r\n'
    b'                        state = self._request_states.get(rid)\r\n'
    b'                        meta = self._request_metadata.get(rid)\r\n'
    b'                        if state == "sent" and meta is not None:\r\n'
    b'                            bi = meta.get("batch_index", -1)\r\n'
    b'                            if bi == expected:\r\n'
    b'                                matched_request_id = rid\r\n'
    b'                                matched_batch_index = bi\r\n'
    b'                                break\r\n'
    b'\r\n'
    b'            if matched_request_id is None:\r\n'
    b'                # No matching "sent" request for the expected batch index.\r\n'
    b'                # This is a stale (arrived after timeout) or out-of-order response.\r\n'
    b'                # Ignore it \xe2\x80\x94 do not advance state or release a deferred batch.\r\n'
    b'                print(\r\n'
    b'                    f"[wifi_bridge] [WP1] Discarding 0x08 for slot={slot}: "\r\n'
    b'                    f"no \'sent\' request at batch_index={expected}. "\r\n'
    b'                    f"(stale, duplicate, or out-of-order \xe2\x80\x94 ignored)",\r\n'
    b'                    flush=True,\r\n'
    b'                )\r\n'
    b'                return None'
)

new = (
    b'            # matching 0x08 arrives. So at any point, we are waiting for exactly\r\n'
    b'            # one batch_index, and any other 0x08 is stale/duplicate/out-of-order.\r\n'
    b'            #\r\n'
    b'            # Two cases:\r\n'
    b'            #   1. No pending request for this slot (direct _handle_schema_frame call,\r\n'
    b'            #      e.g. in tests or unknown-slot schema). Accept the response \xe2\x80\x94\r\n'
    b'            #      schema is registered and the function returns the slot.\r\n'
    b'            #   2. A pending request exists but is in the wrong state/batch_index.\r\n'
    b'            #      This is out-of-order/stale/duplicate \xe2\x80\x94 discard without\r\n'
    b'            #      affecting schema registration or slot state.\r\n'
    b'            expected = self._next_expected_batch.get(slot)\r\n'
    b'            matched_request_id = None\r\n'
    b'            matched_batch_index = None\r\n'
    b'            has_pending_request = False\r\n'
    b'            with self._stream_lock:\r\n'
    b'                for rid, req_slot in list(self._request_to_slot.items()):\r\n'
    b'                    if req_slot == slot:\r\n'
    b'                        has_pending_request = True\r\n'
    b'                        state = self._request_states.get(rid)\r\n'
    b'                        meta = self._request_metadata.get(rid)\r\n'
    b'                        if state == "sent" and meta is not None:\r\n'
    b'                            bi = meta.get("batch_index", -1)\r\n'
    b'                            if bi == expected:\r\n'
    b'                                matched_request_id = rid\r\n'
    b'                                matched_batch_index = bi\r\n'
    b'                                break\r\n'
    b'\r\n'
    b'            if has_pending_request and matched_request_id is None:\r\n'
    b'                # A request exists for this slot but no "sent" request matches\r\n'
    b'                # the expected batch_index. This is out-of-order/stale/duplicate.\r\n'
    b'                # Discard \xe2\x80\x94 do not advance state or release a deferred batch.\r\n'
    b'                # Schema is still registered (the firmware accepted the request).\r\n'
    b'                print(\r\n'
    b'                    f"[wifi_bridge] [WP1] Discarding 0x08 for slot={slot}: "\r\n'
    b'                    f"no \'sent\' request at batch_index={expected} "\r\n'
    b'                    f"(stale, duplicate, or out-of-order \xe2\x80\x94 ignored)",\r\n'
    b'                    flush=True,\r\n'
    b'                )\r\n'
    b'                return slot  # schema was registered above; return slot per test contract'
)

if old in content:
    content = content.replace(old, new, 1)
    with open('ground_station/comm/wifi_bridge.py', 'wb') as f:
        f.write(content)
    print("Replacement done")
else:
    print("Pattern NOT FOUND")
    # Find the actual content
    idx = content.find(b'# matching 0x08 arrives')
    if idx >= 0:
        print(repr(content[idx:idx+800]))
    else:
        print("Could not find marker either")
