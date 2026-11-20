import json
import os
import re
import subprocess
import sys
from pathlib import Path

import docx
import pypdf
import pypdfium2 as pdfium

from .store import ROOT, new_id


def process(document):
    path = Path(document["path"])
    suffix = path.suffix.lower()
    blocks = []
    previews = {}
    directory = ROOT / "previews" / document["id"]
    directory.mkdir(parents=True, exist_ok=True)

    def add(text, locator, bbox=None):
        text = text.strip()
        for section in re.findall(r"[\s\S]{1,950}", text):
            blocks.append({"id": new_id(), "text": section, "locator": locator, "bbox": bbox})

    def ocr(image, locator):
        executable = os.getenv("OCR_PYTHON", str(Path(".venv-ocr/Scripts/python.exe").resolve()))
        corrected = directory / f"{image.stem}-corrected.png"
        completed = subprocess.run([executable, "-m", "ocr_worker", str(image), str(corrected)], capture_output=True,
                                   encoding="utf-8", errors="replace", timeout=300,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if completed.returncode:
            raise RuntimeError("OCR 处理失败，请运行 doctor.ps1 检查 PaddleOCR 和模型缓存")
        result = json.loads(completed.stdout)
        if corrected.exists():
            locator += " · 矫正图"
            previews[locator] = str(corrected)
        for region in result["blocks"]:
            add(region["text"], locator, region.get("bbox") if corrected.exists() else None)

    if suffix == ".doc":
        tool = os.getenv("SOFFICE", r"C:\Program Files\LibreOffice\program\soffice.exe")
        subprocess.run([tool, "--headless", "--convert-to", "docx", "--outdir", str(directory), str(path)],
                       check=True, capture_output=True, timeout=90)
        path = directory / (path.stem + ".docx")
        suffix = ".docx"
    if suffix == ".pdf":
        source = pypdf.PdfReader(path)
        image_pdf = pdfium.PdfDocument(path)
        try:
            for index, page in enumerate(source.pages):
                locator = f"PDF 第 {index + 1} 页"
                image = directory / f"{index + 1}.jpg"
                image_pdf[index].render(scale=1.6).to_pil().convert("RGB").save(image)
                previews[locator] = str(image)
                text = page.extract_text() or ""
                compact = re.sub(r"\s", "", text)
                if "\ufffd" not in text and (len(compact) >= 35 or (compact and not page.images)):
                    for paragraph in re.split(r"\n\s*\n", text):
                        add(paragraph, locator)
                elif compact or page.images or page.get_contents() is not None:
                    ocr(image, locator)
        finally:
            image_pdf.close()
    elif suffix == ".docx":
        source = docx.Document(path)
        heading = ""
        for index, paragraph in enumerate(source.paragraphs, 1):
            if paragraph.style.name.startswith("Heading"):
                heading = paragraph.text
            add(paragraph.text, f"{heading or '正文'} · 第 {index} 段")
        for index, table in enumerate(source.tables, 1):
            add("\n".join(" | ".join(c.text for c in r.cells) for r in table.rows), f"表格 {index}")
        for index, relation in enumerate(source.part.rels.values(), 1):
            if not relation.is_external and relation.reltype.endswith("/image"):
                part = relation.target_part
                extension = Path(str(part.partname)).suffix.lower()
                if extension in {".png", ".jpeg", ".jpg", ".webp"}:
                    image = directory / f"word-{index}{extension}"
                    image.write_bytes(part.blob)
                    locator = f"内嵌图片 {index}"
                    previews[locator] = str(image)
                    ocr(image, locator)
    else:
        previews["原图"] = str(path)
        ocr(path, "原图")
    if not blocks:
        raise ValueError("未提取到文字，请检查原件")
    return blocks, previews
