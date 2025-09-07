import pdfplumber
import re
import tempfile
import os
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse

app = FastAPI(title="Invoice Extractor API")

# ---------------------------
# PDF → Extract Text (full + secondary view)
# ---------------------------
def pdf_to_text(file_path: str):
    full_text, meta_text = "", ""
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            # Full page text
            full_text += (page.extract_text() or "") + "\n"

            # Secondary view (cropped region to capture extra details)
            w, h = page.width, page.height
            crop_box = (0, 0, w * 0.58, h)
            meta_text += (page.crop(crop_box).extract_text() or "") + "\n"
    return full_text, meta_text


# ---------------------------
# Extract Seller Details (from secondary text)
# ---------------------------
def extract_seller(meta_text: str):
    lines = [l.strip() for l in meta_text.splitlines() if l.strip()]
    seller_section, capture = [], False
    for line in lines:
        if re.search(r"Sold By", line, re.I):
            capture = True
            continue
        if capture and re.search(r"(Billing|Shipping) Address", line, re.I):
            break
        if capture:
            seller_section.append(line)

    seller_name, seller_gst = "Not Found", "Not Found"
    gst_pattern = re.compile(
        r"GST(?:IN)?(?:\s*Registration\s*No)?[:\-]?\s*([0-9A-Z]{15})", re.I
    )

    for l in seller_section:
        low = l.lower()
        if not (low.startswith("pan") or low.startswith("gst")) and seller_name == "Not Found":
            candidate = re.split(r"\*|Billing|\s{2,}", l)[0].strip()
            seller_name = " ".join(candidate.split()[:2]) or "Not Found"
        if seller_gst == "Not Found":
            m = gst_pattern.search(l)
            if m:
                seller_gst = m.group(1).strip()
    return seller_name, seller_gst


# ---------------------------
# Detect CGST/SGST Tax Rates (line by line)
# ---------------------------
def detect_tax_rate(full_text: str):
    lines = [l.strip() for l in full_text.splitlines() if l.strip()]
    cgst_rate, sgst_rate = None, None

    for i, line in enumerate(lines):
        if "CGST" in line.upper():
            match = re.search(r"(\d{1,2})\s*%", line)
            if not match and i + 1 < len(lines):
                match = re.search(r"(\d{1,2})\s*%", lines[i + 1])
            if match:
                cgst_rate = int(match.group(1))

        if "SGST" in line.upper():
            match = re.search(r"(\d{1,2})\s*%", line)
            if not match and i + 1 < len(lines):
                match = re.search(r"(\d{1,2})\s*%", lines[i + 1])
            if match:
                sgst_rate = int(match.group(1))

    if cgst_rate and sgst_rate:
        return cgst_rate  # both equal (e.g. 9%)
    return None


# ---------------------------
# Extract Invoice Data
# ---------------------------
def extract_invoice_data(full_text: str, meta_text: str) -> dict:
    seller_name, seller_gst = extract_seller(meta_text)

    data = {
        "Seller Name": seller_name,
        "Seller GST Number": seller_gst,
        "Invoice No": re.search(r"Invoice Number\s*:\s*([A-Z0-9\-\/]+)", full_text, re.I),
        "Invoice Date": re.search(
            r"Invoice Date\s*:\s*([\d]{1,2}[./-][\d]{1,2}[./-][\d]{2,4})", full_text, re.I
        ),
    }

    data["Invoice No"] = data["Invoice No"].group(1) if data["Invoice No"] else "Not Found"
    data["Invoice Date"] = data["Invoice Date"].group(1) if data["Invoice Date"] else "Not Found"

    # Totals
    totals = re.search(r"TOTAL:\s*₹?\s*([\d,.\s]+)\s*₹?\s*([\d,.\s]+)", full_text, re.I)
    net_match = re.search(
        r"₹\s*([\d,]+(?:\.\d+)?)\s+\d{1,2}%\s*(IGST|CGST|SGST|GST)", full_text, re.I
    )

    net_amount = float(net_match.group(1).replace(",", "")) if net_match else None
    tax, total_amount = (
        (float(totals.group(1).replace(",", "")), float(totals.group(2).replace(",", "")))
        if totals else (0.0, 0.0)
    )

    # Tax % calculation
    tax_rate = detect_tax_rate(full_text)
    if not tax_rate:
        if net_amount and tax:
            tax_rate = round((tax / net_amount) * 100, 2)
        elif total_amount and tax:
            net_est = max(total_amount - tax, 0.01)
            tax_rate = round((tax / net_est) * 100, 2)

    data.update({
        "Net Amount": net_amount,
        "Total Tax": tax,
        "Total Amount": total_amount,
        "Tax %": tax_rate
    })
    return data


# ---------------------------
# API Endpoint
# ---------------------------
@app.post("/upload-invoice/")
async def upload_invoice(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed.")

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
            tmp_file.write(await file.read())
            temp_path = tmp_file.name

        full_text, meta_text = pdf_to_text(temp_path)
        invoice_data = extract_invoice_data(full_text, meta_text)

        os.remove(temp_path)
        return JSONResponse(content={"status": "success", "invoice": invoice_data})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing invoice: {str(e)}")
