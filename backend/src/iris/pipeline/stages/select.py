"""Select: decide which moments are worth keeping, with the best frame or crop and a caption.

Reads:  alignment.json, frames/*.jpg
Writes: selection.json (Selection), visuals/<id>.jpg
Emits:  VISUAL_SELECTED per kept visual.

Plan (architecture section 1, step 5):
  1. Batch aligned moments (several images per call) to keep calls and cost down.
  2. Ask a vision model, with structured output, for each moment: keep or not, an optional crop
     box (in frame pixels), and a short caption.
  3. Render each kept visual by cropping the real frame with OpenCV. The model chooses and
     captions; it never draws (principle 1).
"""

from iris.pipeline.context import StageContext


async def run(ctx: StageContext) -> None:
    # TODO: batch, call ctx.settings.vision_model, validate crops against frame bounds, render
    raise NotImplementedError("select stage")
