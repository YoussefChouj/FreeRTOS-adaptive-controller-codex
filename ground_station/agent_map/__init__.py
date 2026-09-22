"""Deterministic agent map: generator + explain CLI (spec step 1).

    python -m ground_station.agent_map build     # -> docs/agent-map/agent_map.json
    python -m ground_station.agent_map explain gyroxPID

No LLM-generated metadata; everything except docs/agent-map/modules.yaml is
regenerated from ELF / presets / manifests / glossary / firmware sources.
"""
__version__ = "0.1.0"

__all__ = ["ROOT"]