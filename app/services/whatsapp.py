import os
import re
import urllib.request
import json
import logging
from dotenv import load_dotenv
from .invoice_pdf import generate_invoice_pdf

# Ensure environment variables are loaded
load_dotenv()

logger = logging.getLogger(__name__)

# Loaded strictly from environment variables - NO hardcoded secrets
DEFAULT_TOKEN = os.getenv("WHATSAPP_API_TOKEN") or os.getenv("WHATSAPP_ACCESS_TOKEN") or ""
DEFAULT_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID") or ""
DEFAULT_TEMPLATE_NAME = os.getenv("WHATSAPP_TEMPLATE_NAME", "data_udipi_reciept")


def normalize_whatsapp_phone(phone: str, default_country: str = "91") -> str:
    if not phone:
        return ""
    digits = re.sub(r"\D", "", str(phone))
    if len(digits) == 10:
        return f"{default_country}{digits}"
    if len(digits) == 11 and digits.startswith("0"):
        return f"{default_country}{digits[1:]}"
    if len(digits) >= 11:
        return digits
    return digits


def format_whatsapp_bill_text(order_data: dict) -> str:
    rest_name = (order_data.get("restaurant_name") or "DATA UDIPI").upper()
    tbl = order_data.get("table_number") or ""
    cust_phone = order_data.get("customer_phone") or ""
    bill_no = order_data.get("bill_no") or order_data.get("order_id") or ""
    total_amount = float(order_data.get("total_amount") or 0.0)
    payment_method = (order_data.get("payment_method") or "Cash").upper()
    items = order_data.get("items") or []

    lines = [
        f"🧾 *{rest_name}*",
        f"*TAX INVOICE / CASH BILL*",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    if tbl:
        tbl_label = tbl if str(tbl).upper().startswith("T") else f"Table {tbl}"
        lines.append(f"🍽️ *Table:* {tbl_label}")
    if cust_phone:
        clean_p = re.sub(r"\D", "", str(cust_phone))[-10:]
        lines.append(f"👤 *Customer:* +91 {clean_p}")
    if bill_no:
        lines.append(f"🔢 *Bill No:* #{bill_no}")
    lines.append(f"💳 *Payment:* {payment_method} (PAID ✅)")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("*ITEMS ORDERED:*")

    for item in items:
        name = item.get("name") or "Item"
        qty = item.get("quantity") or 1
        price = float(item.get("price") or 0.0)
        lines.append(f"• {qty}× {name} — ₹{price * qty:.2f}")

    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"*TOTAL PAID:* *₹{total_amount:.2f}*")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("🙏 *Thank you for dining with us at Data Udipi!*")

    return "\n".join(lines)


def upload_whatsapp_media(pdf_bytes: bytes, filename: str = "Invoice.pdf", token: str = None, phone_number_id: str = None) -> dict:
    effective_token = token or os.getenv("WHATSAPP_API_TOKEN") or os.getenv("WHATSAPP_ACCESS_TOKEN") or DEFAULT_TOKEN
    effective_phone_id = phone_number_id or os.getenv("WHATSAPP_PHONE_NUMBER_ID") or DEFAULT_PHONE_NUMBER_ID

    if not effective_token:
        return {"success": False, "error": "WhatsApp API token is missing in backend environment"}
    if not effective_phone_id:
        return {"success": False, "error": "WhatsApp Phone Number ID is missing in backend environment"}

    url = f"https://graph.facebook.com/v21.0/{effective_phone_id}/media"
    boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
    crlf = b"\r\n"

    parts = [
        b"--" + boundary.encode() + crlf +
        b'Content-Disposition: form-data; name="messaging_product"' + crlf + crlf +
        b"whatsapp" + crlf,
        b"--" + boundary.encode() + crlf +
        b'Content-Disposition: form-data; name="type"' + crlf + crlf +
        b"application/pdf" + crlf,
        b"--" + boundary.encode() + crlf +
        f'Content-Disposition: form-data; name="file"; filename="{filename}"'.encode() + crlf +
        b"Content-Type: application/pdf" + crlf + crlf,
        pdf_bytes + crlf,
        b"--" + boundary.encode() + b"--" + crlf
    ]
    body = b"".join(parts)

    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {effective_token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            res_data = json.loads(r.read().decode("utf-8"))
            media_id = res_data.get("id")
            return {"success": True, "media_id": media_id}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8")
        logger.warning(f"Meta Media Upload error: {err_body}")
        try:
            err_json = json.loads(err_body)
            meta_err = err_json.get("error", {})
            err_code = meta_err.get("code")
            msg = meta_err.get("message", f"HTTP {e.code}")
            if err_code == 190 or "expired" in msg.lower():
                return {
                    "success": False,
                    "error": "Meta WhatsApp access token has expired (Error 190). Please refresh WHATSAPP_API_TOKEN in backend .env.",
                    "code": 190,
                    "is_token_expired": True,
                }
            return {"success": False, "error": msg, "code": err_code}
        except Exception:
            return {"success": False, "error": f"HTTP {e.code}: {err_body}"}
    except Exception as e:
        logger.error(f"Meta Media Upload exception: {str(e)}")
        return {"success": False, "error": str(e)}


def send_whatsapp_template_with_document(
    phone: str,
    media_id: str,
    filename: str = "Data_Udipi_Invoice.pdf",
    template_name: str = None,
    token: str = None,
    phone_number_id: str = None,
    order_data: dict = None
) -> dict:
    """
    Sends the Meta approved Utility Template (data_udipi_reciept or dataudipi_restaurant_customer_reciept)
    with the generated POS tax invoice PDF attached directly inside the DOCUMENT header.
    Adapts gracefully to templates with or without dynamic body variables.
    """
    effective_token = token or os.getenv("WHATSAPP_API_TOKEN") or os.getenv("WHATSAPP_ACCESS_TOKEN") or DEFAULT_TOKEN
    effective_phone_id = phone_number_id or os.getenv("WHATSAPP_PHONE_NUMBER_ID") or DEFAULT_PHONE_NUMBER_ID
    primary_template = template_name or os.getenv("WHATSAPP_TEMPLATE_NAME") or DEFAULT_TEMPLATE_NAME or "data_udipi_reciept"

    normalized_phone = normalize_whatsapp_phone(phone)
    if not normalized_phone or len(normalized_phone) < 10:
        return {"success": False, "error": "Invalid customer phone number"}

    url = f"https://graph.facebook.com/v21.0/{effective_phone_id}/messages"

    # Extract dynamic parameters if order_data is provided
    cust_name = "Valued Customer"
    bill_no = "BILL"
    table_no = "Dine-In"
    total_amount = "0.00"
    payment_mode = "CASH"

    if order_data:
        cust_name = str(order_data.get("customer_name") or "Valued Customer")
        raw_bill = str(order_data.get("bill_no") or order_data.get("order_id") or "")
        if raw_bill:
            bill_no = raw_bill
        raw_tbl = str(order_data.get("table_number") or "")
        if raw_tbl:
            table_no = raw_tbl if raw_tbl.upper().startswith("T") else f"Table {raw_tbl}"
        raw_tot = float(order_data.get("total_amount") or 0.0)
        total_amount = f"{raw_tot:.2f}"
        payment_mode = str(order_data.get("payment_method") or "CASH").upper()

    header_component = {
        "type": "header",
        "parameters": [
            {
                "type": "document",
                "document": {
                    "id": media_id,
                    "filename": filename
                }
            }
        ]
    }

    body_params_5 = {
        "type": "body",
        "parameters": [
            {"type": "text", "text": cust_name},
            {"type": "text", "text": bill_no},
            {"type": "text", "text": table_no},
            {"type": "text", "text": total_amount},
            {"type": "text", "text": payment_mode},
        ]
    }

    # Sequence of template payload configurations to try:
    candidate_configs = [
        # Candidate 1: Primary template with document header + 5 body variables
        {"name": primary_template, "components": [header_component, body_params_5], "desc": "with 5 body parameters"},
        # Candidate 2: Primary template with document header only
        {"name": primary_template, "components": [header_component], "desc": "header document only"},
        # Candidate 3: Alternative approved template name with document header only
        {"name": "dataudipi_restaurant_customer_reciept", "components": [header_component], "desc": "fallback approved template"},
    ]

    last_error = None
    for cand in candidate_configs:
        t_name = cand["name"]
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": normalized_phone,
            "type": "template",
            "template": {
                "name": t_name,
                "language": {"code": "en"},
                "components": cand["components"]
            }
        }

        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {effective_token}",
                    "Content-Type": "application/json"
                },
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=25) as r:
                res_data = json.loads(r.read().decode("utf-8"))
                msg_id = res_data.get("messages", [{}])[0].get("id")
                logger.info(f"WhatsApp template '{t_name}' ({cand['desc']}) sent successfully: {msg_id}")
                return {
                    "success": True,
                    "message_id": msg_id,
                    "phone": normalized_phone,
                    "template_name": t_name,
                    "raw": res_data
                }
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8")
            logger.warning(f"Meta Template '{t_name}' attempt failed: {err_body}")
            try:
                meta_err = json.loads(err_body).get("error", {})
                code = meta_err.get("code")
                msg = meta_err.get("message", f"HTTP {e.code}")

                # If token expired, abort retry immediately
                if code == 190 or "expired" in msg.lower():
                    return {
                        "success": False,
                        "error": "Meta WhatsApp access token has expired (Error 190). Please refresh WHATSAPP_API_TOKEN in backend .env.",
                        "code": 190,
                        "is_token_expired": True,
                    }

                last_error = {"code": code, "message": msg, "body": err_body}
                # If error indicates parameter mismatch or template not existing, try next candidate
                continue
            except Exception:
                last_error = {"code": e.code, "message": err_body}
                continue
        except Exception as e:
            logger.error(f"Meta Template exception on '{t_name}': {str(e)}")
            last_error = {"message": str(e)}
            continue

    return {
        "success": False,
        "error": last_error.get("message") if last_error else "Failed to dispatch template message",
        "details": last_error,
    }


def send_whatsapp_invoice_bundle(order_data: dict, phone: str = None, token: str = None, phone_number_id: str = None) -> dict:
    """
    Main Automated WhatsApp Bill Flow:
    1. Generates the exact final bill record as a PDF using ReportLab.
    2. Uploads the PDF to Meta WhatsApp Cloud API media endpoint.
    3. Retrieves the media ID.
    4. Sends the approved Meta Utility template (data_udipi_reciept)
       with the PDF attached directly in its DOCUMENT header.
    5. Returns delivery tracking info and PDF access URL.
    """
    target_phone = phone or order_data.get("customer_phone")
    normalized_phone = normalize_whatsapp_phone(target_phone)
    if not normalized_phone or len(normalized_phone) < 10:
        return {"success": False, "error": "Invalid customer phone number for WhatsApp billing"}

    bill_no = str(order_data.get("bill_no") or order_data.get("order_id") or "BILL")
    clean_bill_no = re.sub(r'[^A-Za-z0-9_-]', '_', bill_no)
    filename = f"Data_Udipi_Invoice_{clean_bill_no}.pdf"

    # Ensure static/invoices directory exists
    static_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "static", "invoices")
    os.makedirs(static_dir, exist_ok=True)
    pdf_path = os.path.join(static_dir, filename)

    # 1. Generate final PDF
    pdf_bytes = generate_invoice_pdf(order_data, output_path=pdf_path)

    # 2. Upload PDF to Meta Media API
    upload_res = upload_whatsapp_media(pdf_bytes, filename=filename, token=token, phone_number_id=phone_number_id)
    if not upload_res.get("success"):
        logger.error(f"Media upload failed: {upload_res.get('error')}")
        return {
            "success": False,
            "error": f"Failed to upload bill PDF to Meta WhatsApp: {upload_res.get('error')}",
            "pdf_url": f"/static/invoices/{filename}",
            "filename": filename,
            "is_token_expired": upload_res.get("is_token_expired", False),
        }

    media_id = upload_res.get("media_id")

    # 3. Send Approved Meta Utility Template with PDF in DOCUMENT header
    template_res = send_whatsapp_template_with_document(
        phone=normalized_phone,
        media_id=media_id,
        filename=filename,
        template_name=DEFAULT_TEMPLATE_NAME,
        token=token,
        phone_number_id=phone_number_id,
        order_data=order_data
    )

    return {
        "success": template_res.get("success", False),
        "phone": normalized_phone,
        "media_id": media_id,
        "template_name": template_res.get("template_name") or DEFAULT_TEMPLATE_NAME,
        "message_id": template_res.get("message_id"),
        "error": template_res.get("error"),
        "is_token_expired": template_res.get("is_token_expired", False),
        "pdf_url": f"/static/invoices/{filename}",
        "filename": filename,
        "bill_text": format_whatsapp_bill_text(order_data),
    }
