# Purpose

The dashboard's multi-slot recorder is GUI-only. This skill exists so an agent
can drive the **same proven capture pipeline** from a shell — pre-clear, sequential
subscribe, decode, write CSVs — without launching Tk, without an attached display,
and without a human at the bench.

The captured output is intentionally byte-identical to what the dashboard writes,
so the existing `/multislot-analyze` pipeline reads it without modification.
