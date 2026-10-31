"""Detect moments: sampled frames -> moments where the screen changed and then settled.

Reads:  frames.json (FrameIndex), frames/*.jpg
Writes: moments.json (Moments)
Emits:  MOMENT_FOUND per moment.

Local work only, no model calls (architecture principle 3).

Plan:
  1. Walk frames in order; compute a cheap difference to the previous frame (grayscale, downscaled,
     mean abs diff) plus PySceneDetect content/threshold detection for hard cuts.
  2. A moment opens on a significant change and is "settled" once the diff stays below a threshold
     for N consecutive frames. Pick the representative frame from the settled stretch (e.g. the
     sharpest by Laplacian variance).
  3. Record the bounding box of the changed region (cv2.findContours over the thresholded diff) so
     select can propose a crop.
  4. Merge moments that are too short or near-duplicates of the previous one.
"""

from iris.pipeline.context import StageContext


async def run(ctx: StageContext) -> None:
    # TODO: implement with OpenCV + PySceneDetect inside asyncio.to_thread (CPU bound)
    raise NotImplementedError("detect stage")
