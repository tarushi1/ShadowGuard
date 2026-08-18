"""Show what OCR actually read from a real screenshot.

    .venv/bin/python scripts/inspect_screenshot.py ~/Desktop/Screenshot*.png

Turns "it didn't work" into "here is the character it misread", which is
the difference between an unexplained failure and a measured limitation.
"""
import sys, pathlib, logging
logging.disable(logging.CRITICAL)

from backend.app.detectors import ocr, semantic_detector
from backend.app.pipeline import orchestrator

paths = [pathlib.Path(p).expanduser() for p in sys.argv[1:]]
if not paths:
    sys.exit("usage: inspect_screenshot.py <image>...")

semantic_detector.warm_up()

for path in paths:
    result = ocr.extract_text(path.read_bytes())
    verdict = orchestrator.run_stages(result.text)

    print(f"\n=== {path.name} ===")
    print(f"  size        {result.width}x{result.height}, upscaled {result.upscaled}x")
    print(f"  confidence  {result.confidence:.1%}   words {result.word_count}")
    print(f"  RAW READ    {result.raw_text!r}")
    print(f"  AFTER FIX   {result.text!r}")
    print(f"  VERDICT     {verdict.action.value}  score {verdict.score}")
    print(f"  FOUND       {sorted({f.type for f in verdict.findings}) or 'nothing'}")
