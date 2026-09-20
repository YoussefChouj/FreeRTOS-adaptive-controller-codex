# Host Test Triage Report (COMM & SERVICE)

**Date**: 2026-09-20  
**Status**: Completed  
**Goal**: Resolve 71 failing tests across `ground_station/comm/tests` and `ground_station/service/tests`.

---

## Summary of Counts

| Class | Description | Count |
|:---|:---|:---:|
| **Class A** | Regressions from recent host edits (fixed code/tests per protocol doc) | 7 |
| **Class B** | Specs for features never implemented (skipped per instructions) | 62 |
| **Class C** | Stale tests of behavior intentionally changed (updated tests) | 2 |
| **Total** | | **71** |

---

## Classification & Action Table

| # | Test Identifier | Class | Action Taken |
|---|:---|:---:|:---|
| 1 | `ground_station/comm/tests/test_mavlink_limit.py::test_downlink_real` | C | Updated combined rate assertion threshold: `min_combined = 10.0 if has_subscribe else 40.0` to reflect 100 Hz Send_Task cadence (25 Hz subscribe stream at divider 4). |
| 2 | `ground_station/comm/tests/test_subscribe_batching.py::TestWifiBridgeOneRequestPerSlot::test_30_ranges_single_request` | B | Marked class with `@pytest.mark.skip("spec for unimplemented subscribe transaction layer; see reports/COMM-TEST-TRIAGE.md")`. |
| 3 | `ground_station/comm/tests/test_subscribe_batching.py::TestWifiBridgeOneRequestPerSlot::test_31_ranges_single_request_uart5` | B | Skipped with class-level marker (unimplemented `subscribe_slot`). |
| 4 | `ground_station/comm/tests/test_subscribe_batching.py::TestWifiBridgeOneRequestPerSlot::test_31_ranges_single_request_usart3` | B | Skipped with class-level marker (unimplemented `subscribe_slot`). |
| 5 | `ground_station/comm/tests/test_subscribe_batching.py::TestWifiBridgeOneRequestPerSlot::test_54_ranges_single_request_usart3` | B | Skipped with class-level marker (unimplemented `subscribe_slot`). |
| 6 | `ground_station/comm/tests/test_subscribe_batching.py::TestWifiBridgeOneRequestPerSlot::test_62_ranges_single_request_usart3` | B | Skipped with class-level marker (unimplemented `subscribe_slot`). |
| 7 | `ground_station/comm/tests/test_subscribe_batching.py::TestWifiBridgeOneRequestPerSlot::test_63_ranges_exceeds_limit_raises` | B | Skipped with class-level marker (unimplemented `subscribe_slot`). |
| 8 | `ground_station/comm/tests/test_subscribe_batching.py::TestWifiBridgeOneRequestPerSlot::test_caller_divider_preserved` | B | Skipped with class-level marker (unimplemented `subscribe_slot`). |
| 9 | `ground_station/comm/tests/test_subscribe_batching.py::TestWifiBridgeOneRequestPerSlot::test_caller_slot_preserved` | B | Skipped with class-level marker (unimplemented `subscribe_slot`). |
| 10 | `ground_station/comm/tests/test_subscribe_batching.py::TestWifiBridgeOneRequestPerSlot::test_caller_transport_preserved` | B | Skipped with class-level marker (unimplemented `subscribe_slot`). |
| 11 | `ground_station/comm/tests/test_subscribe_batching.py::TestWifiBridgeOneRequestPerSlot::test_diagnostics_ring_populated` | B | Skipped with class-level marker (unimplemented `subscribe_slot`). |
| 12 | `ground_station/comm/tests/test_subscribe_batching.py::TestWifiBridgeOneRequestPerSlot::test_no_pending_batches_remain` | B | Skipped with class-level marker (unimplemented `subscribe_slot`). |
| 13 | `ground_station/comm/tests/test_subscribe_batching.py::TestWifiBridgeOneRequestPerSlot::test_one_range_single_request` | B | Skipped with class-level marker (unimplemented `subscribe_slot`). |
| 14 | `ground_station/comm/tests/test_subscribe_batching.py::TestWifiBridgeOneRequestPerSlot::test_request_ids_are_unique` | B | Skipped with class-level marker (unimplemented `subscribe_slot`). |
| 15 | `ground_station/comm/tests/test_subscribe_batching.py::TestWifiBridgeOneRequestPerSlot::test_request_states_populated` | B | Skipped with class-level marker (unimplemented `subscribe_slot`). |
| 16 | `ground_station/comm/tests/test_subscribe_batching.py::TestWifiBridgeOneRequestPerSlot::test_slot0_with_explicit_ranges_no_override` | B | Skipped with class-level marker (unimplemented `subscribe_slot`). |
| 17 | `ground_station/comm/tests/test_subscribe_batching.py::TestWifiBridgeOneRequestPerSlot::test_timeout_ns_recorded_in_metadata` | B | Skipped with class-level marker (unimplemented `subscribe_slot`). |
| 18 | `ground_station/comm/tests/test_subscribe_batching.py::TestWifiBridgeOneRequestPerSlot::test_zero_ranges_stop_request` | B | Skipped with class-level marker (unimplemented `subscribe_slot`). |
| 19 | `ground_station/comm/tests/test_subscribe_batching.py::TestCoalescing::test_coalesce_adjacent_ranges` | B | Marked class with `@pytest.mark.skip("spec for unimplemented subscribe transaction layer; see reports/COMM-TEST-TRIAGE.md")`. |
| 20 | `ground_station/comm/tests/test_subscribe_batching.py::TestCoalescing::test_coalesce_different_sizes_unchanged` | B | Skipped with class-level marker (unimplemented `_coalesce_ranges`). |
| 21 | `ground_station/comm/tests/test_subscribe_batching.py::TestCoalescing::test_coalesce_empty` | B | Skipped with class-level marker (unimplemented `_coalesce_ranges`). |
| 22 | `ground_station/comm/tests/test_subscribe_batching.py::TestCoalescing::test_coalesce_non_adjacent_unchanged` | B | Skipped with class-level marker (unimplemented `_coalesce_ranges`). |
| 23 | `ground_station/comm/tests/test_subscribe_batching.py::TestCoalescing::test_coalesce_overlapping_ranges` | B | Skipped with class-level marker (unimplemented `_coalesce_ranges`). |
| 24 | `ground_station/comm/tests/test_subscribe_batching.py::TestCoalescing::test_coalesce_preserves_count` | B | Skipped with class-level marker (unimplemented `_coalesce_ranges`). |
| 25 | `ground_station/comm/tests/test_subscribe_batching.py::TestSubscribeTransactionLifecycle::test_counter_increment_deterministic` | B | Marked class with `@pytest.mark.skip("spec for unimplemented subscribe transaction layer; see reports/COMM-TEST-TRIAGE.md")`. |
| 26 | `ground_station/comm/tests/test_subscribe_batching.py::TestSubscribeTransactionLifecycle::test_matching_schema_releases` | B | Skipped with class-level marker (unimplemented `subscribe_slot`). |
| 27 | `ground_station/comm/tests/test_subscribe_batching.py::TestSubscribeTransactionLifecycle::test_one_batch_schema_transitions_to_schema_received` | B | Skipped with class-level marker (unimplemented `subscribe_slot`). |
| 28 | `ground_station/comm/tests/test_subscribe_batching.py::TestSubscribeTransactionLifecycle::test_reconnect_clears_subscriptions` | B | Skipped with class-level marker (unimplemented `subscribe_slot`). |
| 29 | `ground_station/comm/tests/test_subscribe_batching.py::TestSubscribeTransactionLifecycle::test_retry_exhaustion_produces_degraded` | B | Skipped with class-level marker (unimplemented `subscribe_slot`). |
| 30 | `ground_station/comm/tests/test_subscribe_batching.py::TestSubscribeTransactionLifecycle::test_retry_uses_new_request_id` | B | Skipped with class-level marker (unimplemented `subscribe_slot`). |
| 31 | `ground_station/comm/tests/test_subscribe_batching.py::TestSubscribeTransactionLifecycle::test_same_slot_replaces` | B | Skipped with class-level marker (unimplemented `subscribe_slot`). |
| 32 | `ground_station/comm/tests/test_subscribe_batching.py::TestSubscribeTransactionLifecycle::test_schema_identity_change_detected` | B | Skipped with class-level marker (unimplemented `subscribe_slot`). |
| 33 | `ground_station/comm/tests/test_subscribe_batching.py::TestSubscribeTransactionLifecycle::test_stop_clears_all_request_state` | B | Skipped with class-level marker (unimplemented `subscribe_slot`). |
| 34 | `ground_station/comm/tests/test_subscribe_batching.py::TestSubscribeTransactionLifecycle::test_timeout_marks_request_failed` | B | Skipped with class-level marker (unimplemented `subscribe_slot`). |
| 35 | `ground_station/comm/tests/test_subscribe_batching.py::TestSubscribeTransactionLifecycle::test_wrong_slot_schema_ignored` | B | Skipped with class-level marker (unimplemented `subscribe_slot`). |
| 36 | `ground_station/comm/tests/test_subscribe_batching.py::TestDashboardLayoutBatching::test_dashboard_batches_evenly` | B | Marked test with `@pytest.mark.skip("spec for unimplemented subscribe transaction layer; see reports/COMM-TEST-TRIAGE.md")`. |
| 37 | `ground_station/comm/tests/test_transport_state_machine.py::TestOneRequestPerSlot::test_metadata_complete` | B | File marked with module-level `pytest.skip(...)` (unimplemented state machine). |
| 38 | `ground_station/comm/tests/test_transport_state_machine.py::TestOneRequestPerSlot::test_pending_ranges_cleared_after_schema` | B | File marked with module-level `pytest.skip(...)`. |
| 39 | `ground_station/comm/tests/test_transport_state_machine.py::TestOneRequestPerSlot::test_reconnect_clears_subscriptions` | B | File marked with module-level `pytest.skip(...)`. |
| 40 | `ground_station/comm/tests/test_transport_state_machine.py::TestOneRequestPerSlot::test_request_id_unique_per_subscribe` | B | File marked with module-level `pytest.skip(...)`. |
| 41 | `ground_station/comm/tests/test_transport_state_machine.py::TestOneRequestPerSlot::test_same_slot_replaces` | B | File marked with module-level `pytest.skip(...)`. |
| 42 | `ground_station/comm/tests/test_transport_state_machine.py::TestOneRequestPerSlot::test_schema_identity_changed_flag_propagated` | B | File marked with module-level `pytest.skip(...)`. |
| 43 | `ground_station/comm/tests/test_transport_state_machine.py::TestOneRequestPerSlot::test_schema_identity_tracking` | B | File marked with module-level `pytest.skip(...)`. |
| 44 | `ground_station/comm/tests/test_transport_state_machine.py::TestOneRequestPerSlot::test_schema_response_transitions_sent_to_schema_received` | B | File marked with module-level `pytest.skip(...)`. |
| 45 | `ground_station/comm/tests/test_transport_state_machine.py::TestOneRequestPerSlot::test_slot_state_transitions` | B | File marked with module-level `pytest.skip(...)`. |
| 46 | `ground_station/comm/tests/test_transport_state_machine.py::TestOneRequestPerSlot::test_stop_clears_request_state` | B | File marked with module-level `pytest.skip(...)`. |
| 47 | `ground_station/comm/tests/test_transport_state_machine.py::TestOneRequestPerSlot::test_subscribe_sends_one_request` | B | File marked with module-level `pytest.skip(...)`. |
| 48 | `ground_station/comm/tests/test_transport_state_machine.py::TestSchemaFrameProcessing::test_correct_slot_accepted` | B | File marked with module-level `pytest.skip(...)`. |
| 49 | `ground_station/comm/tests/test_transport_state_machine.py::TestSchemaFrameProcessing::test_schema_frame_clears_pending_ranges` | B | File marked with module-level `pytest.skip(...)`. |
| 50 | `ground_station/comm/tests/test_transport_state_machine.py::TestSchemaFrameProcessing::test_wrong_slot_ignored` | B | File marked with module-level `pytest.skip(...)`. |
| 51 | `ground_station/comm/tests/test_wifi_bridge_dataframe.py::TestDataBufFrame::test_50b_frame_not_decoded` | A | Removed legacy 50-53 B fallback decoding block in `wifi_bridge._parse_one` so partial-frame wait behavior is restored. |
| 52 | `ground_station/comm/tests/test_wifi_bridge_dataframe.py::TestDataBufFrame::test_52b_frame_no_longer_decoded` | A | Fixed by removing 50-53 B fallback block in `wifi_bridge._parse_one`. |
| 53 | `ground_station/comm/tests/test_wifi_bridge_dataframe.py::TestDataBufFrame::test_53b_frame_not_decoded` | A | Fixed by removing 50-53 B fallback block in `wifi_bridge._parse_one`. |
| 54 | `ground_station/comm/tests/test_wifi_bridge_stream.py::TestCrcValidation::test_corrupted_crc_is_rejected` | A | Added CRC16-CCITT check over `frame[2:-2]` in `wifi_bridge._decode_stream_frame`, dropping corrupted frames. |
| 55 | `ground_station/comm/tests/test_wifi_bridge_stream.py::TestCrcValidation::test_crc_error_payload_contains_crc_errors_field` | A | Added `slot{slot}.crc_errors` to returned json payload in `wifi_bridge._decode_stream_frame`. |
| 56 | `ground_station/comm/tests/test_wifi_bridge_stream.py::TestCrcValidation::test_crc_validation_does_not_misalign_buffer` | A | Fixed by CRC16-CCITT rejection in `wifi_bridge._decode_stream_frame`. |
| 57 | `ground_station/comm/tests/test_wifi_bridge_stream.py::TestCrcValidation::test_second_corrupted_frame_increments_crc_errors` | A | Added `stats["crc_errors"] += 1` increment under `_stream_lock` on CRC mismatch in `wifi_bridge._decode_stream_frame`. |
| 58 | `ground_station/comm/tests/test_wifi_bridge_stream.py::TestSchemaRenegotiation::test_pending_ranges_cleared_by_schema_frame` | B | Marked class with `@pytest.mark.skip("spec for unimplemented subscribe transaction layer; see reports/COMM-TEST-TRIAGE.md")`. Also added defensive `_pending_schema_ranges.pop(slot, None)` in `wifi_bridge._handle_schema_frame`. |
| 59 | `ground_station/comm/tests/test_wifi_bridge_stream.py::TestSchemaRenegotiation::test_slot_state_advanced_to_sent_on_new_request` | B | Skipped with class-level marker (unimplemented `_slot_states`). |
| 60 | `ground_station/comm/tests/test_wifi_bridge_stream.py::TestSchemaRenegotiation::test_stale_schema_cleared_on_send` | B | Skipped with class-level marker (unimplemented `subscribe_slot`). |
| 61 | `ground_station/comm/tests/test_wifi_bridge_stream.py::TestStreamStateMachine::test_first_data_frame_transitions_schema_received_to_streaming` | B | Marked class with `@pytest.mark.skip("spec for unimplemented subscribe transaction layer; see reports/COMM-TEST-TRIAGE.md")`. |
| 62 | `ground_station/comm/tests/test_wifi_bridge_stream.py::TestStreamStateMachine::test_schema_frame_transitions_sent_to_schema_received` | B | Skipped with class-level marker (unimplemented `_slot_states`). |
| 63 | `ground_station/comm/tests/test_wifi_bridge_stream.py::TestStreamStateMachine::test_schema_received_never_regresses_to_sent` | B | Skipped with class-level marker (unimplemented `_slot_states`). |
| 64 | `ground_station/comm/tests/test_wifi_bridge_stream.py::TestStreamStateMachine::test_streaming_never_regresses_to_schema_received` | B | Skipped with class-level marker (unimplemented `_slot_states`). |
| 65 | `ground_station/comm/tests/test_wifi_bridge_stream.py::TestStreamMetadataCrcErrors::test_stream_metadata_includes_crc_errors` | B | Marked class with `@pytest.mark.skip("spec for unimplemented subscribe transaction layer; see reports/COMM-TEST-TRIAGE.md")`. |
| 66 | `ground_station/comm/tests/test_wifi_bridge_stream.py::TestStreamMetadataCrcErrors::test_stream_metadata_zero_crc_errors_when_no_errors` | B | Skipped with class-level marker (unimplemented `_stream_metadata`). |
| 67 | `ground_station/comm/tests/test_wifi_bridge_stream.py::TestReconnectSequence::test_new_subscribe_clears_stats_and_schema` | B | Marked test with `@pytest.mark.skip("spec for unimplemented subscribe transaction layer; see reports/COMM-TEST-TRIAGE.md")`. |
| 68 | `ground_station/comm/tests/test_wifi_bridge_stream.py::TestReconnectSequence::test_reconnect_only_clears_target_slot` | B | Marked test with `@pytest.mark.skip("spec for unimplemented subscribe transaction layer; see reports/COMM-TEST-TRIAGE.md")`. |
| 69 | `ground_station/comm/tests/test_wifi_bridge_stream.py::TestReconnectSequence::test_stats_persist_after_stop` | B | Marked test with `@pytest.mark.skip("spec for unimplemented subscribe transaction layer; see reports/COMM-TEST-TRIAGE.md")`. |
| 70 | `ground_station/comm/tests/test_wifi_bridge_stream.py::TestStaleSchemaDetection::test_reconnect_drops_frames_with_old_schema` | B | Marked test with `@pytest.mark.skip("spec for unimplemented subscribe transaction layer; see reports/COMM-TEST-TRIAGE.md")`. |
| 71 | `ground_station/service/tests/test_service.py::test_http_subscribe_preview_endpoint` | C | Updated expected preview rate from `20.0` to `25.0` (divider 4 with `SUBSCRIBE_SEND_TASK_HZ = 100`). |
