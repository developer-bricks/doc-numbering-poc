from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from io import BytesIO
from typing import Iterator, Optional, Tuple
import requests
import re
import uuid

app = FastAPI()


class DocumentRequest(BaseModel):
    file_url: str


@app.get("/")
def home():
    return {"message": "Backend is working"}


# -----------------------------
# Helpers
# -----------------------------

def normalize_file_url(file_url: str) -> str:
    file_url = file_url.strip()

    if file_url.startswith("//"):
        file_url = "https:" + file_url

    if not file_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Invalid file URL")

    return file_url


def download_file(file_url: str) -> bytes:
    try:
        response = requests.get(file_url, timeout=30)
        response.raise_for_status()
        return response.content
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not download file: {str(e)}")


def load_docx(file_bytes: bytes) -> Document:
    try:
        return Document(BytesIO(file_bytes))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not open docx file: {str(e)}")


def iter_paragraphs_in_table(table: Table) -> Iterator[Paragraph]:
    for row in table.rows:
        for cell in row.cells:
            for paragraph in cell.paragraphs:
                yield paragraph
            for nested_table in cell.tables:
                yield from iter_paragraphs_in_table(nested_table)


def iter_all_paragraphs(doc: Document) -> Iterator[Paragraph]:
    # Normal body paragraphs
    for paragraph in doc.paragraphs:
        yield paragraph

    # Paragraphs inside tables
    for table in doc.tables:
        yield from iter_paragraphs_in_table(table)


def replace_paragraph_text(paragraph: Paragraph, new_text: str) -> None:
    """
    Safest simple replacement for most contract docs.
    Keeps paragraph object/styling container, but replaces run text content.
    """
    if not paragraph.runs:
        paragraph.text = new_text
        return

    paragraph.runs[0].text = new_text
    for run in paragraph.runs[1:]:
        run.text = ""


# -----------------------------
# Detection rules
# -----------------------------

HEADING_PATTERNS = [
    # 1. DEFINITIONS
    # 1. Definitions
    re.compile(r"^\s*(\d+)\.\s+(.+?)\s*$"),

    # 1 Definitions
    re.compile(r"^\s*(\d+)\s+([A-Za-z].+?)\s*$"),

    # Section 1: Definitions
    # Section 1 - Definitions
    re.compile(r"^\s*Section\s+(\d+)\s*[:\-]\s*(.+?)\s*$", re.IGNORECASE),

    # Article 1: Definitions
    # Article 1 - Definitions
    re.compile(r"^\s*Article\s+(\d+)\s*[:\-]\s*(.+?)\s*$", re.IGNORECASE),
]

CLAUSE_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)+)\s+(.+?)\s*$")


def looks_like_heading_text(title: str) -> bool:
    """
    Heuristic to reduce false positives.
    Accept headings that are short-ish and look like titles, not full paragraphs.
    """
    cleaned = title.strip()
    if not cleaned:
        return False

    # Too long usually means body text, not heading
    if len(cleaned) > 120:
        return False

    # Heading-like if title case / uppercase / shorter label style
    words = cleaned.split()
    if len(words) > 15:
        return False

    return True


def detect_main_heading(text: str) -> Optional[Tuple[int, str]]:
    """
    Returns (section_number, section_title) if text matches a supported heading format.
    """
    for pattern in HEADING_PATTERNS:
        match = pattern.match(text)
        if not match:
            continue

        section_number = int(match.group(1))
        section_title = match.group(2).strip()

        if looks_like_heading_text(section_title):
            return section_number, section_title

    return None


def detect_clause(text: str) -> Optional[Tuple[str, str]]:
    """
    Returns (original_number, clause_body) if text matches numeric clause pattern.
    """
    match = CLAUSE_PATTERN.match(text)
    if not match:
        return None

    original_number = match.group(1).strip()
    clause_body = match.group(2).strip()

    # Extra safety: clause body should not be empty
    if not clause_body:
        return None

    return original_number, clause_body


# -----------------------------
# Main formatter
# -----------------------------

def format_contract_numbering(doc: Document) -> Document:
    current_section: Optional[int] = None
    sub_counter = 0

    for paragraph in iter_all_paragraphs(doc):
        text = paragraph.text.strip()

        if not text:
            continue

        # 1) Detect heading
        heading = detect_main_heading(text)
        if heading:
            current_section, _section_title = heading
            sub_counter = 0
            continue

        # 2) Detect numeric clause
        clause = detect_clause(text)
        if clause and current_section is not None:
            _old_number, clause_body = clause
            sub_counter += 1
            new_text = f"{current_section}.{sub_counter} {clause_body}"
            replace_paragraph_text(paragraph, new_text)

    return doc


# -----------------------------
# API endpoint
# -----------------------------

@app.post("/format-numbering")
def format_numbering(data: DocumentRequest):
    file_url = normalize_file_url(data.file_url)
    file_bytes = download_file(file_url)
    doc = load_docx(file_bytes)

    formatted_doc = format_contract_numbering(doc)

    output_stream = BytesIO()
    formatted_doc.save(output_stream)
    output_stream.seek(0)

    unique_name = f"formatted_{uuid.uuid4().hex[:8]}.docx"

    return StreamingResponse(
        output_stream,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": f'attachment; filename="{unique_name}"'
        }
    )