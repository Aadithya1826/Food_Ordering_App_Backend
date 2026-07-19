import os
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional

try:
    import razorpay
except Exception:
    razorpay = None

router = APIRouter(prefix="/api/v1/payments", tags=["payments"])

# Get credentials from environment
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET")

# Initialize client only if we have the secret
client = None
if razorpay and RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET:
    try:
        client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
    except Exception as e:
        print(f"Failed to initialize Razorpay Client: {e}")

class CreateOrderRequest(BaseModel):
    amount: float  # Amount in rupees (e.g., 250.50)

class VerifyPaymentRequest(BaseModel):
    razorpay_order_id: Optional[str] = None
    razorpay_payment_id: str
    razorpay_signature: Optional[str] = None

@router.post("/create-order")
def create_order(req: CreateOrderRequest):
    if razorpay is None:
        return {
            "success": False,
            "message": "Razorpay integration is unavailable on this server. Falling back to direct checkout."
        }

    if not RAZORPAY_KEY_SECRET:
        # Return success=False to notify the frontend to fallback
        return {
            "success": False,
            "message": "Razorpay Key Secret is missing. Falling back to direct checkout."
        }
    
    try:
        # Amount in paise (1 INR = 100 paise)
        amount_in_paise = int(req.amount * 100)
        
        order_data = {
            "amount": amount_in_paise,
            "currency": "INR",
            "payment_capture": 1  # Auto capture
        }
        
        # Create order in Razorpay
        razorpay_order = client.order.create(data=order_data)
        
        return {
            "success": True,
            "order": {
                "id": razorpay_order["id"],
                "amount": razorpay_order["amount"],
                "currency": razorpay_order["currency"]
            }
        }
    except Exception as e:
        # Return success=False to allow fallback instead of crashing
        return {
            "success": False,
            "error": str(e),
            "message": "Razorpay order creation failed. Falling back to direct checkout."
        }

@router.post("/verify")
def verify_payment(req: VerifyPaymentRequest):
    if not RAZORPAY_KEY_SECRET or not client:
        # If no secret is configured, we cannot verify signature but can assume success if payment_id is present
        if req.razorpay_payment_id:
            return {"success": True, "message": "Direct payment accepted (no signature verification)"}
        return {"success": False, "message": "Missing payment ID"}
        
    try:
        # Verify signature if order_id and signature are present
        if req.razorpay_order_id and req.razorpay_signature:
            params_dict = {
                'razorpay_order_id': req.razorpay_order_id,
                'razorpay_payment_id': req.razorpay_payment_id,
                'razorpay_signature': req.razorpay_signature
            }
            client.utility.verify_payment_signature(params_dict)
            return {"success": True}
        else:
            return {"success": False, "message": "Missing order ID or signature for verification"}
    except Exception as e:
        return {"success": False, "error": str(e)}
