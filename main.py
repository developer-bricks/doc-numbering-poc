from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from docx import Document
from docx.shared import Inches
import requests
import tempfile
import uuid
import os
import re

app = FastAPI()

# For local testing keep this as localhost.
# After Render deployment, replace with your Render URL.
BASE_URL = os.getenv("BASE_URL", "https://doc-numbering-poc.onrender.com")

class DocumentRequest(BaseModel):
    file_url: str


@app.get("/")
def home():
    return {"message": "Backend is working"}


def is_uppercase_heading(text: str) -> bool:
    """
    Detect headings like:
    1. DEFINITIONS
    2. PAYMENT TERMS
    """
    m = re.match(r"^(\d+)\.\s+(.+)$", text)
    if not m:
        return False

    rest = m.group(2).strip()
    return rest == rest.upper()


def get_indent_level(text: str, last_numeric_level: int = 0) -> int | None:
    """
    Return indentation level based on the clause prefix.
    We DO NOT change the numbering text itself.

    Examples:
    1.1         -> level 1
    1.1.1       -> level 2
    1.1.1.1     -> level 3
    (a)         -> level after numeric block
    (i)         -> one level deeper than (a)
    (A)         -> treated as an alphabetic nested level
    i. / ii.    -> roman level
    a. / b.     -> alphabetic level
    """

    # Skip main uppercase headings like "1. DEFINITIONS"
    if is_uppercase_heading(text):
        return 0

    # Numeric clauses like:
    # 1.1
    # 1.1.1
    # 1.1.1.1
    numeric_match = re.match(r"^(\d+(?:\.\d+)+)\s+", text)
    if numeric_match:
        prefix = numeric_match.group(1)
        # dots count = nesting depth
        return prefix.count(".")

    # Parenthesized lowercase roman: (i), (ii), (iv)
    if re.match(r"^\(([ivxlcdm]+)\)\s+", text, re.IGNORECASE):
        return last_numeric_level + 2

    # Parenthesized lowercase alpha: (a), (b)
    if re.match(r"^\(([a-z])\)\s+", text):
        return last_numeric_level + 1

    # Parenthesized uppercase alpha: (A), (B)
    if re.match(r"^\(([A-Z])\)\s+", text):
        return max(1, last_numeric_level + 1)

    # Bare lowercase roman with dot: i. ii. iii.
    if re.match(r"^([ivxlcdm]+)\.\s+", text, re.IGNORECASE):
        return last_numeric_level + 2

    # Bare lowercase alpha with dot: a. b.
    if re.match(r"^([a-z])\.\s+", text):
        return last_numeric_level + 1

    # Top-level numeric heading like "1. Something" but not uppercase heading
    # Keep it at base level
    if re.match(r"^\d+\.\s+", text):
        return 0

    return None


def apply_indentation(paragraph, level: int):
    """
    Apply visual indentation only.
    Adjust these values if client wants tighter/looser spacing.
    """
    if level <= 0:
        paragraph.paragraph_format.left_indent = Inches(0)
        paragraph.paragraph_format.first_line_indent = Inches(0)
        return

    # Each level moves slightly to the right
    paragraph.paragraph_format.left_indent = Inches(0.3 * level)
    paragraph.paragraph_format.first_line_indent = Inches(0)


@app.post("/format-numbering")
def format_numbering(data: DocumentRequest):
    file_url = data.file_url.strip()

    if file_url.startswith("//"):
        file_url = "https:" + file_url

    if not file_url.startswith("http://") and not file_url.startswith("https://"):
        raise HTTPException(status_code=400, detail="Invalid file URL")

    # Download file from Bubble
    try:
        response = requests.get(file_url)
        response.raise_for_status()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not download file: {str(e)}")

    # Save input temporarily
    with tempfile.NamedTemporaryFile(delete=False, suffix=".docx") as tmp_input:
        tmp_input.write(response.content)
        input_path = tmp_input.name

    # Open docx
    try:
        doc = Document(input_path)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not open docx file: {str(e)}")

    last_numeric_level = 0

    for p in doc.paragraphs:
        text = p.text.strip()

        if not text:
            continue

        level = get_indent_level(text, last_numeric_level)

        if level is None:
            continue

        # Keep track of numeric depth so patterns like (a), (i) can nest under it
        numeric_match = re.match(r"^(\d+(?:\.\d+)+)\s+", text)
        if numeric_match:
            last_numeric_level = numeric_match.group(1).count(".")
        elif is_uppercase_heading(text):
            last_numeric_level = 0

        apply_indentation(p, level)

    # Save output temporarily
    output_filename = f"formatted_{uuid.uuid4()}.docx"
    output_path = os.path.join(tempfile.gettempdir(), output_filename)
    doc.save(output_path)

    return {
        "message": "Indentation formatted successfully",
        "processed_file": output_filename,
        "download_url": f"{BASE_URL}/download/{output_filename}"
    }


@app.get("/download/{filename}")
def download_file(filename: str):
    file_path = os.path.join(tempfile.gettempdir(), filename)

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(
        path=file_path,
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
