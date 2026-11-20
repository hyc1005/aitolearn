"""Run one local document OCR job and print machine-readable blocks."""
import contextlib
import json
import sys

from PIL import Image

with contextlib.redirect_stdout(sys.stderr):
    from paddleocr import PPStructureV3

    pipeline = PPStructureV3(device="cpu", use_doc_orientation_classify=True,
                             use_doc_unwarping=True, use_textline_orientation=True)
    if sys.argv[1:] == ["--warmup"]:
        sys.exit(0)
    results = list(pipeline.predict(sys.argv[1]))
blocks = []
for result in results:
    preprocessing = result.get("doc_preprocessor_res") or {}
    image = preprocessing.get("output_img")
    if image is not None:
        Image.fromarray(image[:, :, ::-1]).save(sys.argv[2])
    parsed = result.json
    for block in parsed.get("res", parsed).get("parsing_res_list", []):
        value = block.get("block_content", "").strip()
        if value:
            # PP-Structure coordinates are for the preprocessed image.
            blocks.append({"text": value, "bbox": block.get("block_bbox"), "coordinate_space": "preprocessed"})
print(json.dumps({"blocks": blocks}, ensure_ascii=True))
