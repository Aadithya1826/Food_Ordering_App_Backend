from pydantic import BaseModel

class InventoryUpdate(BaseModel):
    quantity: float