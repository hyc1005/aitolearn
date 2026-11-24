"""Bounded inspection of explicitly uploaded chat attachments; no filesystem paths."""
import io
import zipfile
from pathlib import Path

MATERIALS = {'.pdf', '.docx', '.doc', '.jpg', '.jpeg', '.png', '.webp'}


def archive_is_small(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        items = archive.infolist()
        return len(items) <= 2000 and sum(item.file_size for item in items) <= 48_000_000


def material_signature(suffix, raw):
    if suffix == '.docx':
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            return archive_is_small(raw) and 'word/document.xml' in archive.namelist()
    return {'.pdf': raw.startswith(b'%PDF-'), '.doc': raw.startswith(bytes.fromhex('d0cf11e0a1b11ae1')),
            '.png': raw.startswith(b'\x89PNG\r\n\x1a\n'), '.jpg': raw.startswith(b'\xff\xd8\xff'),
            '.jpeg': raw.startswith(b'\xff\xd8\xff'), '.webp': raw.startswith(b'RIFF') and raw[8:12] == b'WEBP'}.get(suffix, False)


def excerpt(name, raw):
    suffix = Path(name).suffix.lower()
    if len(raw) > 5_000_000:
        return ''
    if suffix in {'.txt', '.csv', '.md'}:
        try:
            return raw.decode('utf-8-sig')[:1800]
        except UnicodeDecodeError:
            return raw.decode('gb18030', errors='replace')[:1800]
    if suffix == '.pdf' and material_signature(suffix, raw):
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(raw))
        return '\n'.join(page.extract_text() or '' for page in reader.pages[:2])[:1800]
    if suffix == '.docx' and material_signature(suffix, raw):
        from docx import Document
        return '\n'.join(p.text for p in Document(io.BytesIO(raw)).paragraphs[:40])[:1800]
    return ''
