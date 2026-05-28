"""Aetheryte — hybrid recall + redaction network for CRYSTALIUM.

The Aetheryte is the index/retrieval layer of the memory harness.
  aetheryte/recall.py  — BM25 ⊕ vector ⊕ graph hybrid recall (W3)
  aetheryte/redact.py  — regex pre-pass + LLM judge (W2)
"""
