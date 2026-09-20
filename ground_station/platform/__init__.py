"""Firmware platform discovery and generated registry access."""

from .discovery import Discovery, RegistryDigest, discover, load_generated_registry
from .transactions import Command, Outcome, RejectReason, Result, TransactionLedger
from .telemetry import TelemetrySchema, TelemetryStream, load_telemetry_schema
from .experiments import ExperimentEvent, ExperimentRun, ExperimentRuntime, ExperimentState
from .resources import ResourceEntry, ResourceMap, RuntimeMetric, build_resource_map, observe_metrics
from .shell import start_shell
from .firmware_contract import (
    FirmwareContract, FirmwareContract as Contract,
    COMMAND_TABLE, CONTRACT_VERSION, GS_PROTO_VERSION,
    PLATFORM_COMMAND_VERSION,
    SUBSCRIBE_MAX_SLOTS, SUBSCRIBE_MAX_RANGES, SUBSCRIBE_STREAM_MAX_BYTES,
    SUBSCRIBE_SEND_TASK_HZ, SUBSCRIBE_BUDGET_PCT_USART3, SUBSCRIBE_BUDGET_PCT_UART5,
    frame_size_subscribe_request, frame_size_schema_reply, frame_size_data_frame,
    effective_rate_hz, link_budget_usart3, link_budget_uart5,
    TELEMETRY_FRAMES, current as firmware_contract,
)

__all__ = [
    "Discovery", "RegistryDigest", "discover", "load_generated_registry",
    "Command", "Outcome", "RejectReason", "Result", "TransactionLedger",
    "TelemetrySchema", "TelemetryStream", "load_telemetry_schema",
    "ExperimentEvent", "ExperimentRun", "ExperimentRuntime", "ExperimentState",
    "ResourceEntry", "ResourceMap", "RuntimeMetric", "build_resource_map", "observe_metrics",
    "start_shell",
    "FirmwareContract", "Contract",
    "COMMAND_TABLE", "CONTRACT_VERSION", "GS_PROTO_VERSION",
    "PLATFORM_COMMAND_VERSION",
    "SUBSCRIBE_MAX_SLOTS", "SUBSCRIBE_MAX_RANGES", "SUBSCRIBE_STREAM_MAX_BYTES",
    "SUBSCRIBE_SEND_TASK_HZ", "SUBSCRIBE_BUDGET_PCT_USART3", "SUBSCRIBE_BUDGET_PCT_UART5",
    "frame_size_subscribe_request", "frame_size_schema_reply", "frame_size_data_frame",
    "effective_rate_hz", "link_budget_usart3", "link_budget_uart5",
    "TELEMETRY_FRAMES",
    "firmware_contract",
]
