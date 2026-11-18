from pypdf import PdfReader
from pypdf.errors import EmptyFileError, FileNotDecryptedError, PdfReadError


def validate_pdf_file(file_path):
    try:
        reader = PdfReader(file_path, strict=True)
        if reader.is_encrypted:
            raise ValueError("Password-protected PDFs cannot be replaced.")
        page_count = len(reader.pages)
    except (EmptyFileError, FileNotDecryptedError, PdfReadError, EOFError) as exc:
        raise ValueError("The uploaded file is not a valid, readable PDF.") from exc

    if page_count == 0:
        raise ValueError("The uploaded PDF does not contain any pages.")

    return page_count
