from io import StringIO
from pdfminer.converter import TextConverter
from pdfminer.layout import LAParams
from pdfminer.pdfinterp import PDFResourceManager, PDFPageInterpreter
from pdfminer.pdfpage import PDFPage


def extract_text_from_pdf(path: str) -> str:
    with open(path, 'rb') as file_handle:
        resource_manager = PDFResourceManager()
        text_output = StringIO()
        converter = TextConverter(resource_manager, text_output, laparams=LAParams())
        interpreter = PDFPageInterpreter(resource_manager, converter)

        for page in PDFPage.get_pages(file_handle, caching=True, check_extractable=True):
            interpreter.process_page(page)

        extracted_text = text_output.getvalue()
        text_output.close()
        converter.close()

    return extracted_text.replace('\x0c', '\n')


def convert_text_into_dict(text: str) -> dict:
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    title = lines[0] if lines else "Tài liệu đề nghị xây dựng văn bản"
    main_content = "\n".join(lines[1:]) if len(lines) > 1 else text
    return {
        "Title": title,
        "Abstract": "",
        "Figures/Tables Captions": "",
        "Main Content": main_content,
        "Appendix": "",
    }
