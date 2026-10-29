"""
1. Ingest layer: source adapters -> event table -> clean / idempotent / time normalize.

N6 (2026-10-25): grouped by work pipeline. This layer only turns raw traces
into the unified event table; it emits nothing meant for human reading.
"""
