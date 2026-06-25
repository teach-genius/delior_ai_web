from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import docx
import fitz
from dotenv import load_dotenv
from llama_cloud_services import LlamaParse

load_dotenv()
logger = logging.getLogger(__name__)

# ─── Constantes ───────────────────────────────────────────────────

SUPPORTED_FORMATS = {".pdf", ".docx", ".doc", ".jpg", ".jpeg", ".png"}
IMAGE_FORMATS     = {".jpg", ".jpeg", ".png"}
WORD_FORMATS      = {".docx", ".doc"}
MIN_TEXT_CHARS    = 100

# ─── LlamaParse singleton ─────────────────────────────────────────

def _get_llama_parser() -> LlamaParse:
    api_key = os.getenv("LLAMA_CLOUD_API_KEY")
    if not api_key:
        raise RuntimeError("LLAMA_CLOUD_API_KEY manquant dans .env")
    return LlamaParse(
        api_key=api_key,
        result_type="text",
        parsing_instruction="""
            Extract all text from this CV using OCR.

            Requirements:
            - Keep original layout (paragraphs, line breaks)
            - Maintain sections if possible (Education, Experience, Skills)
            - Do not rephrase anything

            Output: plain text            
            """
    )

# ─── OCR LlamaParse ───────────────────────────────────────────────

async def _ocr(path: str) -> str:
    loop   = asyncio.get_running_loop()
    parser = _get_llama_parser()
    docs   = await loop.run_in_executor(None, parser.load_data, path)
    return "\n\n".join(d.text for d in docs if d.text.strip())

# ─── Extracteurs par format ───────────────────────────────────────

async def _from_pdf(path: str) -> str:
    with fitz.open(path) as doc:
        pages = [p.get_text().strip() for p in doc]
    text = "\n\n".join(filter(None, pages))
    if len(text) >= MIN_TEXT_CHARS:
        return text
    logger.info("PDF scanné → OCR LlamaParse")
    return await _ocr(path)


async def _from_word(path: str) -> str:
    loop   = asyncio.get_running_loop()
    suffix = Path(path).suffix.lower()

    if suffix == ".docx":
        text = await loop.run_in_executor(None, _extract_docx, path)
    else:
        text = await loop.run_in_executor(None, _extract_doc, path)

    return text if text.strip() else await _ocr(path)


def _extract_docx(path: str) -> str:
    return "\n".join(
        p.text for p in docx.Document(path).paragraphs if p.text.strip()
    )


def _extract_doc(path: str) -> str:
    tmp = tempfile.mkdtemp()
    try:
        result = subprocess.run(
            ["soffice", "--headless", "--convert-to", "docx", "--outdir", tmp, path],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Conversion .doc échouée : {result.stderr.strip()}")
        converted = Path(tmp) / f"{Path(path).stem}.docx"
        return _extract_docx(str(converted))
    except FileNotFoundError:
        raise RuntimeError("LibreOffice requis pour les .doc")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

# ─── Pipeline principal ───────────────────────────────────────────

async def cv_to_text(path: str) -> str:
    """
    Pipeline d'extraction de texte brut depuis un CV.

    PDF   → fitz natif  │  scanné → LlamaParse OCR
    DOCX  → python-docx │  vide   → LlamaParse OCR
    DOC   → LibreOffice → python-docx
    Image → LlamaParse OCR
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"Fichier introuvable : {path}")

    suffix = Path(path).suffix.lower()
    if suffix not in SUPPORTED_FORMATS:
        raise ValueError(f"Format non supporté : {suffix}")

    if suffix == ".pdf":
        text = await _from_pdf(path)
    elif suffix in WORD_FORMATS:
        text = await _from_word(path)
    else:
        text = await _ocr(path)

    if not text.strip():
        logger.warning("Texte vide extrait de : %s", Path(path).name)

    return text.strip()
