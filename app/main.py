import os
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from .routes import auth, menu, orders, table, inventory, restaurants, reports, customer, recipes, customer_delivery, delivery_assignments, delivery_tracking, payments, rider
from .mcp import router as mcp_router

import logging

logger = logging.getLogger(__name__)

app = FastAPI()

@app.get("/health")
def health_check():
    return {"status": "ok"}

# Ensure static/images directory exists
os.makedirs(os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "images"), exist_ok=True)
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")), name="static")

# CORS Middleware Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",     # React development server
        "http://localhost:5173",     # Vite development server
        "http://localhost:8081",     # Expo development server
        "http://localhost:8082",     # Expo development server (secondary)
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:8081",
        "http://127.0.0.1:8082",
        "http://frontend:3000",      # Docker container
        "http://dev-adm-ui.dataudipi.com",
        "https://dev-adm-ui.dataudipi.com",
        "http://dev-cus-ui.dataudipi.com",
        "https://dev-cus-ui.dataudipi.com",
        "http://dev-ui.dataudipi.com",
        "https://dev-ui.dataudipi.com"
    ],
    allow_origin_regex=".*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)

# Global handler so CORS headers are present even on unhandled 500 errors.
# Without this, CORSMiddleware does not attach headers to error responses and
# the browser reports a misleading "CORS policy" error instead of the real one.
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}")
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
