# S3 — Transactional commands and events

Add versioned command envelopes, transaction IDs, acknowledgements, rejection
reasons, applied-state reporting, idempotence, and structured event frames.
Migrate one low-risk command family first, then the rest. Gate every command in
simulation and hardware with safe rejection tests.

