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


def page_render_scale(page):
    """Avoid upscaling a full-page scan beyond its original pixel density."""
    width, height = page.get_size()
    for image in page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_IMAGE]):
        left, bottom, right, top = image.get_bounds()
        image_width, image_height = right - left, top - bottom
        if width > 0 and height > 0 and image_width > 0 and image_height > 0 and (
                image_width * image_height >= width * height * .8):
            try:
                pixels_width, pixels_height = image.get_px_size()
            except (pdfium.PdfiumError, ValueError, TypeError):
                return 1.6
            native_scale = min(pixels_width / image_width, pixels_height / image_height)
            return max(1.0, min(1.6, native_scale))
    return 1.6


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
        # A retry must never attach coordinates to a corrected image from an older run.
        corrected.unlink(missing_ok=True)
        try:
            completed = subprocess.run([executable, "-m", "ocr_worker", str(image), str(corrected)], capture_output=True,
                                       encoding="utf-8", errors="replace", timeout=600,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"{locator} OCR 处理超时，请检查图片大小或本机资源后重试") from None
        except OSError:
            raise RuntimeError("OCR 环境无法启动，请运行 doctor.ps1 检查 Python 和 PaddleOCR") from None
        if completed.returncode:
            raise RuntimeError("OCR 处理失败，请运行 doctor.ps1 检查 PaddleOCR 和模型缓存")
        try:
            result = json.loads(completed.stdout)
            regions = result["blocks"]
            if not isinstance(regions, list) or any(not isinstance(region, dict) or not isinstance(region.get("text"), str)
                                                    for region in regions):
                raise ValueError
        except (ValueError, TypeError, KeyError):
            raise RuntimeError("OCR 返回结果格式无效，请检查 OCR 环境后重试") from None
        if corrected.exists():
            locator += " · 矫正图"
            previews[locator] = str(corrected)
        metrics["ocr_engine"] = "PaddleOCR"
        metrics["ocr_units"] += 1
        for region in regions:
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
                    text_page = page.get_textpage()
                    try:
                        text = text_page.get_text_bounded()
                    finally:
                        text_page.close()
                    compact = re.sub(r"\s", "", text)
                    has_image = any(page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_IMAGE]))
                    text_usable = "\ufffd" not in text and (len(compact) >= 35 or (compact and not has_image))
                    bitmap = page.render(scale=1.6 if text_usable else page_render_scale(page))
                    try:
                        bitmap.to_pil().convert("RGB").save(image)
                    finally:
                        bitmap.close()
                    previews[locator] = str(image)
                    if text_usable:
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
