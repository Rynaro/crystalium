"""Dream — async consolidation worker + scheduler for CRYSTALIUM.

Exports:
  DreamScheduler — dual-trigger idle-poll + session_end with G8 dedup.
  DreamWorker    — orient → gather → consolidate → prune cycle.
"""

from crystalium.dream.scheduler import DreamScheduler
from crystalium.dream.worker import DreamWorker

__all__ = ["DreamScheduler", "DreamWorker"]
