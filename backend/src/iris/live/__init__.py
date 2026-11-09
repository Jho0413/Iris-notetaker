"""Live processing: the pipeline's work done while a browser live session is recording.

``LiveRegistry`` (owned by the job
runner) holds one ``LiveProcessor`` per recording session; each runs its lanes over a shared
``LiveTimeline``.
"""

from iris.live.processor import LiveProcessor, LiveRegistry
from iris.live.timeline import Lane, LiveTimeline

__all__ = ["Lane", "LiveProcessor", "LiveRegistry", "LiveTimeline"]
