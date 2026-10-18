import os
import hmac
import hashlib
import json
import logging
# pyrefly: ignore[missing-import]
from fastapi import APIRouter, HTTPException, Depends, Request
# pyrefly: ignore[missing-import]
from pydantic import BaseModel
from typing import Optional
# pyrefly: ignore[missing-import]
from sqlalchemy.orm import Session
from ..db import SessionLocal
from ..models.order import Order
from ..models.dine_in_session import DineInSession

logger = logging.getLogger(__name__)

try:
    # pyrefly: ignore[missing-import]
    import razorpay
except Exception:
    razorpay = None

router = APIRouter(prefix="/api/v1/payments", tags=["payments"])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET")
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET")

client = None
if razorpay and RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET:
    try:
        client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
    except Exception as e:
        logger.error(f"Failed to initialize Razorpay Client: {e}")

class CreateOrderRequest(BaseModel):
    model_config = {"extra": "ignore"}
    amount: Optional[float] = None
    order_id: Optional[int] = None
    receipt: Optional[str] = None

class VerifyPaymentRequest(BaseModel):
    model_config = {"extra": "ignore"}
    razorpay_order_id: Optional[str] = None
    razorpay_payment_id: str
    razorpay_signature: Optional[str] = None
    order_id: Optional[int] = None

@router.post("/create-order")
def create_order(req: CreateOrderRequest, db: Session = Depends(get_db)):
    if razorpay is None or not RAZORPAY_KEY_SECRET or not client:
        return {
            "success": False,
            "message": "Razorpay integration is unavailable or unconfigured on this server. Falling back to direct checkout."
        }
    
    try:
        # Never trust frontend amount when order_id is provided
        if req.order_id:
            db_order = db.query(Order).filter(Order.id == req.order_id).first()
            if not db_order:
                raise HTTPException(status_code=404, detail="Order not found")
            if (db_order.payment_status or "").upper() == "PAID":
                raise HTTPException(status_code=400, detail="A settled bill must not be settled again.")
            amount_in_paise = int(round(float(db_order.total_amount or 0.0) * 100))
        elif req.amount is not None and req.amount > 0:
            amount_in_paise = int(round(req.amount * 100))
        else:
            raise HTTPException(status_code=400, detail="Valid order_id or positive amount is required.")
        
        order_data = {
            "amount": amount_in_paise,
            "currency": "INR",
            "receipt": req.receipt or (f"ord_{req.order_id}" if req.order_id else None),
            "payment_capture": 1
        }
        
        razorpay_order = client.order.create(data=order_data)
        
        return {
            "success": True,
            "order": {
                "id": razorpay_order["id"],
                "amount": razorpay_order["amount"],
                "currency": razorpay_order["currency"]
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Razorpay order creation error: {e}")
        return {
            "success": False,
            "error": str(e),
            "message": "Razorpay order creation failed. Falling back to direct checkout."
        }

@router.post("/verify")
def verify_payment(req: VerifyPaymentRequest, db: Session = Depends(get_db)):
    # Concurrency safe check on order
    db_order = None
    if req.order_id:
        db_order = db.query(Order).filter(Order.id == req.order_id).with_for_update().first()
        if not db_order:
            raise HTTPException(status_code=404, detail="Order not found")
        # Idempotency: A settled bill must not be settled again
        if (db_order.payment_status or "").upper() == "PAID":
            return {"success": True, "message": "Payment already confirmed (idempotent)"}

    if not RAZORPAY_KEY_SECRET or not client:
        if req.razorpay_payment_id:
            if db_order:
                db_order.payment_status = "Paid"
                db_order.payment_method = "Razorpay"
                if db_order.status == "PENDING":
                    db_order.status = "CONFIRMED"
                db.commit()
            return {"success": True, "message": "Direct payment accepted (no signature verification configured)"}
        return {"success": False, "message": "Missing payment ID"}
        
    try:
        if req.razorpay_order_id and req.razorpay_signature:
            params_dict = {
                'razorpay_order_id': req.razorpay_order_id,
                'razorpay_payment_id': req.razorpay_payment_id,
                'razorpay_signature': req.razorpay_signature
            }
            client.utility.verify_payment_signature(params_dict)

            if db_order:
                db_order.payment_status = "Paid"
                db_order.payment_method = "Razorpay"
                if db_order.status == "PENDING":
                    db_order.status = "CONFIRMED"
                db.commit()

            return {"success": True, "message": "Payment verified successfully"}
        else:
            return {"success": False, "message": "Missing order ID or signature for verification"}
    except Exception as e:
        if db_order:
            db.rollback()
        logger.error(f"Razorpay verification failed: {e}")
        return {"success": False, "error": str(e)}

@router.post("/webhook")
async def razorpay_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Secure Razorpay webhook endpoint with HMAC signature verification and
    idempotent event processing.
    """
    body_bytes = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")

    if not RAZORPAY_WEBHOOK_SECRET:
        logger.warning("RAZORPAY_WEBHOOK_SECRET not configured. Rejecting webhook.")
        raise HTTPException(status_code=400, detail="Webhook secret not configured")

    expected_sig = hmac.new(
        RAZORPAY_WEBHOOK_SECRET.encode(),
        body_bytes,
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(signature, expected_sig):
        logger.warning("Invalid Razorpay webhook signature rejected")
        raise HTTPException(status_code=400, detail="Invalid signature")

    try:
        data = json.loads(body_bytes.decode())
        event = data.get("event")
        payload = data.get("payload", {})
        payment_entity = payload.get("payment", {}).get("entity", {})
        notes = payment_entity.get("notes", {})
        order_id_str = notes.get("order_id") or payment_entity.get("receipt", "").replace("ord_", "")

        if event in ["payment.captured", "order.paid"] and order_id_str and order_id_str.isdigit():
            order_id = int(order_id_str)
            db_order = db.query(Order).filter(Order.id == order_id).with_for_update().first()
            if db_order and (db_order.payment_status or "").upper() != "PAID":
                db_order.payment_status = "Paid"
                db_order.payment_method = "Razorpay"
                if db_order.status == "PENDING":
                    db_order.status = "CONFIRMED"
                db.commit()
                logger.info(f"Razorpay webhook: Order #{order_id} marked as Paid via {event}")

        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Webhook processing error: {e}")
        return {"status": "error", "detail": str(e)}

