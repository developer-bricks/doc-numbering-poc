from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import requests
from docx import Document
import re
from io import BytesIO

app = FastAPI()

class DocumentRequest(BaseModel):
    file_url: str

@app.get("/")
def home():
    return {"message": "Backend is working"}

@app.post("/format-numbering")
def format_numbering(data: DocumentRequest):
    file_url = data.file_url.strip()

    if file_url.startswith("//"):
        file_url = "https:" + file_url

    if not file_url.startswith("http://") and not file_url.startswith("https://"):
        raise HTTPException(status_code=400, detail="Invalid file URL")

    try:
        response = requests.get(file_url)
        response.raise_for_status()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not download file: {str(e)}")

    try:
        input_stream = BytesIO(response.content)
        doc = Document(input_stream)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not open docx file: {str(e)}")

    current_section = None
    sub_counter = 0

    for p in doc.paragraphs:
        text = p.text.strip()

        if not text:
            continue

        # Match main section headings like: 1. DEFINITIONS
        section_match = re.match(r"^(\d+)\.\s+(.+)$", text)
        if section_match:
            after_number = section_match.group(2).strip()

            # Only treat as section heading if the text after the number is uppercase
            if after_number == after_number.upper():
                current_section = int(section_match.group(1))
                sub_counter = 0
                continue

        # Match numbered clauses like:
        # 1.1 text
        # 1.1.1 text
        # 1.1.1.1 text
        clause_match = re.match(r"^\d+(?:\.\d+)+\s+(.*)$", text)
        if clause_match and current_section is not None:
            sub_counter += 1
            clause_body = clause_match.group(1).strip()
            p.text = f"{current_section}.{sub_counter} {clause_body}"

    output_stream = BytesIO()
    doc.save(output_stream)
    output_stream.seek(0)

    return StreamingResponse(
        output_stream,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": "attachment; filename=formatted_document.docx"
        }
    )