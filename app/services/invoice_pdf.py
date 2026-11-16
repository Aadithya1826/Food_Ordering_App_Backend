import os
import io
import datetime
from reportlab.lib.pagesizes import portrait
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.graphics.shapes import Drawing, Rect, String, Line
from reportlab.graphics.barcode import qr

def generate_invoice_pdf(order_data: dict, output_path: str = None) -> bytes:
    """
    Generates a pixel-perfect restaurant Tax Invoice PDF matching Data Udipi bill design.
    """
    items = order_data.get('items', [])
    if not items:
        items = [{'name': 'Dine-In Food Order', 'quantity': 1, 'price': float(order_data.get('total_amount', 0.0))}]

    # Calculate dynamic height based on item count
    base_height = 680
    items_height = len(items) * 20
    page_width = 280
    page_height = base_height + items_height

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=(page_width, page_height))

    # Background
    c.setFillColor(colors.HexColor('#FFFFFF'))
    c.rect(0, 0, page_width, page_height, fill=True, stroke=False)

    y = page_height

    # 1. Top Food Banner
    banner_height = 80
    y -= banner_height
    # Draw warm banner background
    c.setFillColor(colors.HexColor('#2A170A'))
    c.rect(0, y, page_width, banner_height, fill=True, stroke=False)

    # Decorative header graphics (warm gradient simulation)
    c.setFillColor(colors.HexColor('#3D200F'))
    c.rect(0, y, page_width, banner_height * 0.4, fill=True, stroke=False)

    # Text overlay "40 YEARS OF EXCELLENCE"
    c.setFont('Helvetica-Bold', 11)
    c.setFillColor(colors.HexColor('#F5EBE1'))
    c.drawCentredString(page_width / 2.0, y + 34, "4 0   Y E A R S   O F   E X C E L L E N C E")

    c.setStrokeColor(colors.HexColor('#D97706'))
    c.setLineWidth(1)
    c.line(40, y + 26, page_width - 40, y + 26)

    # 2. Hanging Logo Signboard
    y -= 20
    # Chains
    c.setStrokeColor(colors.HexColor('#78716C'))
    c.setLineWidth(1.5)
    c.line(page_width / 2.0 - 36, y + 20, page_width / 2.0 - 36, y + 8)
    c.line(page_width / 2.0 + 36, y + 20, page_width / 2.0 + 36, y + 8)

    # Signboard
    sign_w, sign_h = 100, 24
    sign_x = (page_width - sign_w) / 2.0
    sign_y = y - 16
    c.setFillColor(colors.HexColor('#15803D'))
    c.roundRect(sign_x, sign_y, sign_w, sign_h, 4, fill=True, stroke=True)
    c.setStrokeColor(colors.HexColor('#166534'))
    c.setLineWidth(1)
    c.roundRect(sign_x + 2, sign_y + 2, sign_w - 4, sign_h - 4, 3, fill=False, stroke=True)

    c.setFont('Helvetica-Bold', 11)
    c.setFillColor(colors.HexColor('#FFFFFF'))
    c.drawCentredString(page_width / 2.0, sign_y + 7, "DATA UDIPI")

    y = sign_y - 12

    # 3. Location & Legal Details
    c.setFont('Helvetica-Bold', 7.5)
    c.setFillColor(colors.HexColor('#1C1917'))
    c.drawCentredString(page_width / 2.0, y, "Data Udipi : MGR Nagar, Nesapakkam, Chennai, Tamil Nadu 600078")
    y -= 11

    c.setFont('Helvetica-Bold', 8)
    c.drawCentredString(page_width / 2.0, y, "Data Udipi Limited")
    y -= 9

    c.setFont('Helvetica', 6.5)
    c.setFillColor(colors.HexColor('#44403C'))
    c.drawCentredString(page_width / 2.0, y, "Place Of Supply : Data Udipi - 51, Anna Main Rd, Ponnambalam Colony,")
    y -= 8
    c.drawCentredString(page_width / 2.0, y, "MGR Nagar, Nesapakkam, Chennai, Tamil Nadu 600078.")
    y -= 8
    c.drawCentredString(page_width / 2.0, y, "Regd. Office: Chennai.")
    y -= 9

    c.setFont('Helvetica-Bold', 7.5)
    c.setFillColor(colors.HexColor('#1C1917'))
    c.drawCentredString(page_width / 2.0, y, "GSTIN NO: 29AAACT1836J1ZC")
    y -= 14

    # 4. TAX INVOICE Title
    c.setFont('Helvetica-Bold', 11)
    c.drawCentredString(page_width / 2.0, y, "TAX INVOICE")
    y -= 12

    # 5. Metadata Row
    bill_no = str(order_data.get('bill_no') or order_data.get('order_id') or 'DU104-1001')
    if not bill_no.startswith('DU'):
        bill_no = f"DU104-{bill_no}"
    
    date_str = order_data.get('date_str') or datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    counter_tbl = str(order_data.get('table_number') or '4')
    if not counter_tbl.upper().startswith('T') and counter_tbl.isdigit():
        counter_tbl = f"Table {counter_tbl}"
    cust_phone = str(order_data.get('customer_phone') or '9840810585')
    clean_phone = ''.join(filter(str.isdigit, cust_phone))[-10:] if cust_phone else '9840810585'

    margin = 14
    content_w = page_width - (margin * 2)

    c.setFont('Helvetica-Bold', 6.5)
    c.drawString(margin, y, "INVOICE NO :")
    c.setFont('Helvetica', 6.5)
    c.drawString(margin + 48, y, bill_no)
    c.drawRightString(page_width - margin, y, date_str)
    y -= 9

    c.setFont('Helvetica-Bold', 6.5)
    c.drawString(margin, y, "COUNTER / TBL :")
    c.setFont('Helvetica', 6.5)
    c.drawString(margin + 58, y, counter_tbl)
    y -= 9

    c.setFont('Helvetica-Bold', 6.5)
    c.drawString(margin, y, "CUSTOMER :")
    c.setFont('Helvetica', 6.5)
    c.drawString(margin + 48, y, str(order_data.get('customer_name') or 'REGISTERED'))
    y -= 9

    c.setFont('Helvetica-Bold', 6.5)
    c.drawString(margin, y, "MOBILE NO :")
    c.setFont('Helvetica', 6.5)
    c.drawString(margin + 48, y, clean_phone)
    y -= 10

    # Dotted line
    c.setStrokeColor(colors.HexColor('#A8A29E'))
    c.setLineWidth(0.6)
    c.setDash(2, 2)
    c.line(margin, y, page_width - margin, y)
    c.setDash() # reset dash
    y -= 10

    # 6. Items Table Header
    c.setFont('Helvetica-Bold', 7)
    c.setFillColor(colors.HexColor('#1C1917'))
    c.drawString(margin, y, "DESCRIPTION")
    c.drawRightString(page_width - margin - 85, y, "PRICE")
    c.drawRightString(page_width - margin - 45, y, "QTY")
    c.drawRightString(page_width - margin, y, "NET AMT")
    y -= 8

    c.setDash(1.5, 1.5)
    c.line(margin, y, page_width - margin, y)
    c.setDash()
    y -= 11

    # 7. Item Rows
    total_qty = 0
    total_amount = float(order_data.get('total_amount') or 0.0)

    for item in items:
        name = str(item.get('name') or 'Item').upper()
        qty = int(item.get('quantity') or 1)
        price = float(item.get('price') or 0.0)
        net_amt = price * qty
        total_qty += qty

        c.setFont('Helvetica', 7)
        # truncate long item names if needed
        display_name = name[:20] + '..' if len(name) > 22 else name
        c.drawString(margin, y, display_name)
        c.drawRightString(page_width - margin - 85, y, f"₹{price:.2f}")
        c.drawRightString(page_width - margin - 45, y, f"{qty:03d}")
        c.drawRightString(page_width - margin, y, f"₹{net_amt:.2f}")
        y -= 12

    y += 2
    c.setDash(1.5, 1.5)
    c.line(margin, y, page_width - margin, y)
    c.setDash()
    y -= 10

    # 8. Totals Section
    subtotal = float(order_data.get('subtotal') or total_amount)
    discount = float(order_data.get('discount') or 0.0)

    c.setFont('Helvetica-Bold', 7)
    c.drawString(margin, y, "GROSS TOTAL :")
    c.drawRightString(page_width - margin, y, f"₹{subtotal:.2f}")
    y -= 9

    c.drawString(margin, y, "DISCOUNT TOTAL :")
    c.drawRightString(page_width - margin, y, f"₹{discount:.2f}")
    y -= 9

    c.setFont('Helvetica-Bold', 7.5)
    c.drawString(margin, y, "TOTAL INVOICE AMOUNT :")
    c.drawRightString(page_width - margin, y, f"₹{total_amount:.2f}")
    y -= 14

    # 9. Payment & Delivery
    c.setFont('Helvetica-Bold', 9.5)
    c.drawString(margin, y, "PAYMENT & DELIVERY")
    y -= 11

    pay_method = str(order_data.get('payment_method') or 'CASH').upper()
    c.setFont('Helvetica-Bold', 7)
    c.drawString(margin, y, pay_method)
    c.drawRightString(page_width - margin, y, f"₹{total_amount:.2f}")
    y -= 9

    c.drawString(margin, y, "TOTAL RECEIVED AMOUNT :")
    c.drawRightString(page_width - margin, y, f"₹{total_amount:.2f}")
    y -= 12

    c.setFont('Helvetica', 6.5)
    c.drawString(margin, y, f"NO OF ITEMS : {len(items):02d}")
    y -= 8
    c.drawString(margin, y, f"TOTAL QTY : {float(total_qty):.2f}")
    y -= 14

    # 10. Footer Disclaimer Notes
    c.setFont('Helvetica', 5.5)
    c.setFillColor(colors.HexColor('#57534E'))
    c.drawCentredString(page_width / 2.0, y, "No return / exchange on prepared food items.")
    y -= 7
    c.drawCentredString(page_width / 2.0, y, "Thank you for dining with us!")
    y -= 7
    c.drawCentredString(page_width / 2.0, y, "Call to Action: Love what's in? Explore more from Instagram")
    y -= 7
    c.drawCentredString(page_width / 2.0, y, "Rewards: DATA UDIPI REWARDS - Scan for loyalty points and offers.")
    y -= 7
    c.drawCentredString(page_width / 2.0, y, "Billing Provider: Digital billing powered by Razorpay")
    y -= 14

    # 11. Follow Us & QR Code
    c.setFont('Helvetica-Bold', 8.5)
    c.setFillColor(colors.HexColor('#1C1917'))
    c.drawCentredString(page_width / 2.0, y, "Follow Us On")
    y -= 6

    qr_size = 62
    qr_x = (page_width - qr_size) / 2.0
    qr_y = y - qr_size

    # Generate QR Code
    qr_code = qr.QrCodeWidget('https://www.instagram.com/data_udipi_restaurant/')
    qr_bounds = qr_code.getBounds()
    qr_w = qr_bounds[2] - qr_bounds[0]
    qr_h = qr_bounds[3] - qr_bounds[1]

    d = Drawing(qr_size, qr_size, transform=[qr_size / qr_w, 0, 0, qr_size / qr_h, 0, 0])
    d.add(qr_code)
    from reportlab.graphics import renderPDF
    renderPDF.draw(d, c, qr_x, qr_y)

    y = qr_y - 12
    c.setFont('Helvetica-Bold', 7.5)
    c.setFillColor(colors.HexColor('#BE185D'))
    c.drawCentredString(page_width / 2.0, y, "@DATA_UDIPI_RESTAURANT")
    y -= 8

    # Instagram bottom gradient bar
    c.setFillColor(colors.HexColor('#E1306C'))
    c.rect(0, 0, page_width, 4, fill=True, stroke=False)
    c.setFillColor(colors.HexColor('#F77737'))
    c.rect(page_width * 0.5, 0, page_width * 0.5, 4, fill=True, stroke=False)

    c.showPage()
    c.save()

    pdf_bytes = buffer.getvalue()
    if output_path:
        with open(output_path, 'wb') as f:
            f.write(pdf_bytes)

    return pdf_bytes

if __name__ == '__main__':
    sample_order = {
        'restaurant_name': 'Data Udipi',
        'table_number': '4',
        'customer_name': 'REGISTERED',
        'customer_phone': '9840810585',
        'bill_no': 'DU104-100034372TE',
        'date_str': '2026-08-20 14:12:19',
        'total_amount': 75.0,
        'subtotal': 75.0,
        'discount': 0.0,
        'payment_method': 'Cash',
        'items': [
            {'name': 'CHAPPATHI KURUMA', 'quantity': 1, 'price': 75.0}
        ]
    }
    pdf_data = generate_invoice_pdf(sample_order, 'static/sample_invoice.pdf')
    print('Generated PDF successfully! Size:', len(pdf_data), 'bytes')
