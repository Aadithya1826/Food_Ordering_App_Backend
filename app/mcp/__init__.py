from fastapi import APIRouter
from .routes import router as web_router
from .mobile_routes import router as mobile_router

router = APIRouter()
router.include_router(web_router)
router.include_router(mobile_router)
