from ground_station.comm.wifi_bridge import WifiBridge
from ground_station.platform.transactions import (
    Outcome, RejectReason, Result, build_result,
)


def test_wifi_bridge_decodes_transaction_result_frame():
    bridge = WifiBridge(vofa_enabled=False)
    expected = Result(0x4243, Outcome.REJECTED, 0xFE, 0,
                      RejectReason.UNKNOWN_COMMAND, "command rejected")
    rx = bytearray(build_result(expected))
    decoded = bridge._parse_one(rx)
    assert decoded == ("transaction", expected)
    assert rx == bytearray()


def test_wifi_bridge_rx_loop_queues_transaction_result():
    bridge = WifiBridge(vofa_enabled=False)
    expected = Result(7, Outcome.APPLIED, 1, 0, detail="applied")
    decoded = bridge._parse_one(bytearray(build_result(expected)))
    assert decoded[0] == "transaction"
    bridge._transaction_results.put(decoded[1])
    assert bridge.poll_transaction_result() == expected
