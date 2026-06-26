import fitz


def extract_pdf_text(pdf_path: str):
    doc = fitz.open(pdf_path)
    pages = []

    for page_index, page in enumerate(doc):
        text = page.get_text("text")
        if text.strip():
            pages.append({
                "page": page_index + 1,
                "text": text.strip()
            })

    doc.close()
    return pages
