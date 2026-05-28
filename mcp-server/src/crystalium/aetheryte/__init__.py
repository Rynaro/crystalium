"""Aetheryte — hybrid recall + redaction network for CRYSTALIUM.

The Aetheryte is the index/retrieval layer of the memory harness.
  aetheryte/retrieve.py — BM25 ⊕ vector ⊕ graph hybrid recall with RRF (W3)
  aetheryte/redact.py   — regex pre-pass + LLM judge (W2)

Exports:
  Aetheryte — hybrid retrieval orchestrator.
  Redactor  — PII/secrets redaction (re-exported from redact module).
  rrf_merge — pure RRF fusion function (exported for testing).
"""

from crystalium.aetheryte.redact import Redactor
from crystalium.aetheryte.retrieve import Aetheryte, rrf_merge

__all__ = ["Aetheryte", "Redactor", "rrf_merge"]
