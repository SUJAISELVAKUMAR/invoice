import pdfplumber
import re
import logging
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

pdf_text_store = {"text": ""}

app = FastAPI(title="Invoice Extractor API")



# convert PDF to Text

def pdf_to_text(file_path: str) -> str:
    logger.info(f"Extracting text from PDF: {file_path}")
    text = ""
    with pdfplumber.open(file_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
            logger.debug(f"Extracted page {i} text length: {len(page_text) if page_text else 0}")
    logger.info("PDF extraction completed")
    return text


# Extract Invoice Data

def extract_invoice_data(text: str):
    logger.info("Extracting invoice data from text")
    data = {}

    try:
        # Seller Name
        seller_match = re.search(r"Sold By\s*[:\-]?\s*(.+)", text, re.IGNORECASE)
        data['Seller Name'] = seller_match.group(1).strip() if seller_match else "Not Found"

        # Seller GST Number
        gst_match = re.search(r"GST\s*Registration\s*No\s*[:\-]?\s*([0-9A-Z]{15})", text, re.IGNORECASE)
        data['Seller GST Number'] = gst_match.group(1) if gst_match else "Not Found"

        # Invoice Number
        invoice_no_match = re.search(r"Invoice\s*Number\s*[:\-]?\s*(\S+)", text, re.IGNORECASE)
        data['Invoice No'] = invoice_no_match.group(1) if invoice_no_match else "Not Found"

        # Invoice Date (handles dd.mm.yyyy, dd/mm/yyyy, dd-mm-yyyy)
        date_match = re.search(r"Invoice\s*Date\s*[:\-]?\s*([\d]{1,2}[\/\.\-][\d]{1,2}[\/\.\-][\d]{2,4})", text, re.IGNORECASE)
        data['Invoice Date'] = date_match.group(1) if date_match else "Not Found"

        # Total Tax & Total Amount (same line: TOTAL: ₹79.38 ₹520.38)
        total_line_match = re.search(r"TOTAL:\s*₹?([\d,]+\.\d{2})\s*₹?([\d,]+\.\d{2})", text, re.IGNORECASE)
        if total_line_match:
            data['Total Tax'] = float(total_line_match.group(1).replace(',', ''))
            data['Total Amount'] = float(total_line_match.group(2).replace(',', ''))
        else:
            data['Total Tax'] = None
            data['Total Amount'] = None

    except Exception as e:
        logger.error(f"Error during regex extraction: {e}")
        raise

    logger.info("Invoice data extraction complete")
    return data

# Calculate Tax percen

def calculate_tax_percent(total, tax):
    logger.info("Calculating tax percentage")
    if total and tax:
        return round((tax / total) * 100, 2)
    return None



# Utility: Format Invoice Data for Neat Output

def format_invoice_output(invoice_data: dict, filename: str) -> dict:
    return {
        "status": "success",
        "file": filename,
        "invoice": {
            "seller_name": invoice_data.get("Seller Name"),
            "seller_gst_number": invoice_data.get("Seller GST Number"),
            "invoice_no": invoice_data.get("Invoice No"),
            "invoice_date": invoice_data.get("Invoice Date"),
            "total_tax": (
                f"{invoice_data['Total Tax']:.2f}"
                if invoice_data.get("Total Tax") is not None else None
            ),
            "total_amount": (
                f"{invoice_data['Total Amount']:.2f}"
                if invoice_data.get("Total Amount") is not None else None
            ),
            "tax_percent": (
                f"{invoice_data['Tax %']:.2f}%"
                if invoice_data.get("Tax %") is not None else None
            ),
        }
    }


# ---------------------------
# FastAPI Endpoint
# ---------------------------


@app.post("/upload-invoice/")
async def upload_invoice(file: UploadFile = File(...)):
    logger.info(f"Received file upload: {file.filename}")

    if not file.filename.lower().endswith(".pdf"):
        logger.warning("File is not a PDF")
        raise HTTPException(status_code=400, detail="Only PDF files are allowed.")

    try:
        # Save file temporarily
        temp_path = f"/tmp/{file.filename}"
        with open(temp_path, "wb") as buffer:
            buffer.write(await file.read())
        logger.info(f"File saved at {temp_path}")

        # Extract text & store in global safe variable
        extracted_text = pdf_to_text(temp_path)
        pdf_text_store["text"] = extracted_text

        # Extract structured data
        invoice_data = extract_invoice_data(extracted_text)
        invoice_data['Tax %'] = calculate_tax_percent(
            invoice_data['Total Amount'],
            invoice_data['Total Tax']
        )

        logger.info("Invoice processing complete")
        return JSONResponse(content=format_invoice_output(invoice_data, file.filename))

    except Exception as e:
        logger.error(f"Error processing file: {e}")
        raise HTTPException(status_code=500, detail="Error processing invoice")



# ---------------------------
# Endpoint to fetch stored PDF text
# ---------------------------
@app.get("/get-pdf-text/")
async def get_pdf_text():
    logger.info("Fetching stored PDF text")
    if not pdf_text_store["text"]:
        return {"message": "No PDF text available. Upload a PDF first."}
    return {"pdf_text": pdf_text_store["text"]}