import os
import traceback
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from .routes import auth, menu, orders, table, inventory, restaurants, reports, customer, recipes, customer_delivery, delivery_assignments, delivery_tracking, payments, rider, catering
from .mcp import router as mcp_router

import logging

logger = logging.getLogger(__name__)

app = FastAPI()

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.get("/health/live")
def health_live():
    return {"status": "alive"}

@app.get("/health/ready")
def health_ready():
    from .db import engine
    from sqlalchemy import text
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:
        logger.error(f"Readiness check failed: Database connection error: {e}")
        raise HTTPException(status_code=503, detail="Database not ready")
        
    env = os.getenv("ENVIRONMENT", "development").lower()
    db_url = os.getenv("DATABASE_URL")
    if env == "production" and not db_url:
        raise HTTPException(status_code=503, detail="Configuration missing")

    return {"status": "ready", "database": "connected"}

# Ensure static/images directory exists
os.makedirs(os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "images"), exist_ok=True)
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")), name="static")

# CORS Middleware Configuration
cors_origins_env = os.getenv("CORS_ALLOWED_ORIGINS", "")
allowed_origins = [origin.strip() for origin in cors_origins_env.split(",") if origin.strip()]

# Default development origins
if not allowed_origins and os.getenv("ENVIRONMENT", "development").lower() != "production":
    allowed_origins = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:8081",
        "http://localhost:8082",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:8081",
        "http://127.0.0.1:8082",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins if allowed_origins else ["http://localhost"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)

# Global handler so CORS headers are present even on unhandled errors.
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    if isinstance(exc, HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=getattr(exc, "headers", None)
        )
    logger.error(f"Unhandled exception: {exc}\n{traceback.format_exc()}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error"}
    )
app.include_router(menu.router)
app.include_router(orders.router)
app.include_router(table.router)
app.include_router(inventory.router)
app.include_router(restaurants.router)
app.include_router(reports.router)
app.include_router(mcp_router)
app.include_router(customer.router)
app.include_router(recipes.router)
app.include_router(customer_delivery.router)
app.include_router(delivery_assignments.router)
app.include_router(delivery_tracking.router)
app.include_router(payments.router)
app.include_router(rider.router)
app.include_router(catering.router)
