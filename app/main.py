import os
import traceback
# pyrefly: ignore [missing-import]
from dotenv import load_dotenv

load_dotenv()

# pyrefly: ignore [missing-import]
from fastapi import FastAPI, Request, HTTPException
# pyrefly: ignore [missing-import]
from fastapi.responses import JSONResponse
# pyrefly: ignore [missing-import]
from fastapi.staticfiles import StaticFiles
# pyrefly: ignore [missing-import]
from fastapi.middleware.cors import CORSMiddleware
from .routes import auth, menu, orders, table, inventory, restaurants, reports, customer, recipes, customer_delivery, delivery_assignments, delivery_tracking, payments, rider, catering
from .mcp import router as mcp_router

import logging

logger = logging.getLogger(__name__)

app = FastAPI()

# ─── CORS ─────────────────────────────────────────────────────────────────────
# MUST be registered before any routes or exception handlers so all responses
# (including 4xx/5xx) carry the correct CORS headers.
cors_origins_env = os.getenv("CORS_ALLOWED_ORIGINS", "")
allowed_origins = [origin.strip() for origin in cors_origins_env.split(",") if origin.strip()]

if allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    # No whitelist configured → allow all origins (development / LAN mode)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# ─── Global exception handler ─────────────────────────────────────────────────
# Inject explicit CORS headers so even unhandled 500s are not blocked cross-origin.
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    origin = request.headers.get("origin", "*")
    cors_headers = {
        "Access-Control-Allow-Origin": origin or "*",
        "Access-Control-Allow-Methods": "*",
        "Access-Control-Allow-Headers": "*",
    }
    if isinstance(exc, HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers={**cors_headers, **(getattr(exc, "headers", None) or {})}
        )
    logger.error(f"Unhandled exception: {exc}\n{traceback.format_exc()}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error"},
        headers=cors_headers,
    )

# ─── Health checks ────────────────────────────────────────────────────────────
@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.get("/health/live")
def health_live():
    return {"status": "alive"}

@app.get("/health/ready")
def health_ready():
    from .db import engine
    # pyrefly: ignore [missing-import]
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

# ─── Static files ─────────────────────────────────────────────────────────────
os.makedirs(os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "images"), exist_ok=True)

# pyrefly: ignore [missing-import]
from fastapi.responses import FileResponse, RedirectResponse
from .db import SessionLocal
from .models.menu import MenuItem
import urllib.parse

@app.get("/static/images/{filename}")
async def serve_image(filename: str):
    file_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "images", filename)
    if os.path.exists(file_path):
        return FileResponse(file_path)

    db = SessionLocal()
    try:
        item = db.query(MenuItem).filter(MenuItem.image_url.like(f"%{filename}%")).first()
        dish_name = item.name if item else "Delicious Food"
        encoded_name = urllib.parse.quote(dish_name)
        fallback_url = f"https://image.pollinations.ai/prompt/Delicious%20{encoded_name}%20food%20plating?width=800&height=600&nologo=true"
        return RedirectResponse(url=fallback_url)
    except Exception as e:
        logger.error(f"Fallback image error: {e}")
        return RedirectResponse(url="https://via.placeholder.com/150?text=No+Image")
    finally:
        db.close()

app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")), name="static")

# ─── Routers ──────────────────────────────────────────────────────────────────
app.include_router(auth.router)
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
