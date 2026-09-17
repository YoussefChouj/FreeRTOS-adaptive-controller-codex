"""Firmware platform discovery and generated registry access."""

from .discovery import Discovery, RegistryDigest, discover, load_generated_registry
from .transactions import Command, Outcome, RejectReason, Result, TransactionLedger
from .telemetry import TelemetrySchema, TelemetryStream, load_telemetry_schema
from .experiments import ExperimentEvent, ExperimentRun, ExperimentRuntime, ExperimentState
from .resources import ResourceEntry, ResourceMap, RuntimeMetric, build_resource_map, observe_metrics
from .shell import start_shell

__all__ = [
    "Discovery", "RegistryDigest", "discover", "load_generated_registry",
    "Command", "Outcome", "RejectReason", "Result", "TransactionLedger",
    "TelemetrySchema", "TelemetryStream", "load_telemetry_schema",
    "ExperimentEvent", "ExperimentRun", "ExperimentRuntime", "ExperimentState",
    "ResourceEntry", "ResourceMap", "RuntimeMetric", "build_resource_map", "observe_metrics",
    "start_shell",
]
