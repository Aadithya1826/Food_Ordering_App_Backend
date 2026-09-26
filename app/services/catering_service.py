# pyrefly: ignore [missing-import]
from sqlalchemy.orm import Session
from ..models.catering import CateringSession

def recalculate_catering_session(db: Session, session: CateringSession) -> CateringSession:
    """
    Recalculates all pricing totals for a catering session.
    Must be called whenever guest_count, customizations, or addons change.
    """
    # Base amount
    base_amount = session.package_price * session.guest_count
    
    # Customization amount
    customization_amount = sum(c.total_price_adjustment for c in session.customizations)
    
    # Add-on amount
    addon_amount = sum(a.total_price for a in session.addons)
    
    # Subtotal
    subtotal = base_amount + customization_amount + addon_amount
    
    # Set amounts
    session.base_amount = base_amount
    session.customization_amount = customization_amount
    session.addon_amount = addon_amount
    
    # Calculate taxes and charges
    # Assuming standard GST logic isn't strictly defined yet, we reuse existing fields
    total_amount = (
        subtotal
        + session.transport_charge
        + session.service_charge
        + session.cgst_amount
        + session.sgst_amount
    )
    
    session.total_amount = total_amount
    
    # Advance calculation
    if session.advance_percentage is None:
        session.advance_percentage = 50.0
        
    session.advance_amount = total_amount * session.advance_percentage / 100.0
    session.balance_amount = total_amount - session.advance_amount
    
    db.commit()
    db.refresh(session)
    return session
