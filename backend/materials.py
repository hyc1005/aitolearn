import json
import os
import re
import subprocess
from pathlib import Path

import docx
import pypdfium2 as pdfium

from .store import ROOT, new_id


def ocr_executable():
    return Path(os.getenv("OCR_PYTHON") or ".venv-ocr/Scripts/python.exe").expanduser().resolve()


def process(document, metrics=None):
    if metrics is None:
        metrics = {}
    metrics.update(text_pages=0, ocr_units=0)

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
        executable = ocr_executable()
        if not executable.is_file():
            raise RuntimeError(f"{locator} 需要 OCR，但本机未配置 PaddleOCR。请先安装 OCR 环境，或上传含文字层的 PDF。")
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
        metrics["ocr_engine"] = "PaddleOCR"
        metrics["ocr_units"] += 1
        for region in result["blocks"]:
            add(region["text"], locator, region.get("bbox") if corrected.exists() else None)

    if suffix == ".doc":
        tool = os.getenv("SOFFICE", r"C:\Program Files\LibreOffice\program\soffice.exe")
        subprocess.run([tool, "--headless", "--convert-to", "docx", "--outdir", str(directory), str(path)],
                       check=True, capture_output=True, timeout=90)
        path = directory / (path.stem + ".docx")
        suffix = ".docx"
    if suffix == ".pdf":
        with pdfium.PdfDocument(path) as pdf:
            for index, page in enumerate(pdf):
                try:
                    locator = f"PDF 第 {index + 1} 页"
                    image = directory / f"{index + 1}.jpg"
                    bitmap = page.render(scale=1.6)
                    try:
                        bitmap.to_pil().convert("RGB").save(image)
                    finally:
                        bitmap.close()
                    previews[locator] = str(image)
                    text_page = page.get_textpage()
                    try:
                        text = text_page.get_text_bounded()
                    finally:
                        text_page.close()
                    compact = re.sub(r"\s", "", text)
                    has_image = any(page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_IMAGE]))
                    if "\ufffd" not in text and (len(compact) >= 35 or (compact and not has_image)):
                        for paragraph in re.split(r"\n\s*\n", text):
                            add(paragraph, locator)
                        metrics["text_pages"] += 1
                    elif compact or any(page.get_objects()):
                        ocr(image, locator)
                finally:
                    page.close()
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
