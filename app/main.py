from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from .routes import auth, menu, orders, table, inventory, restaurants, reports
from .mcp import router as mcp_router
import os

app = FastAPI()

# Ensure static/images directory exists
os.makedirs(os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "images"), exist_ok=True)
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")), name="static")

# CORS Middleware Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",     # React development server
        "http://localhost:5173",     # Vite development server
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
        "http://frontend:3000",      # Docker container
        "http://dev-adm-ui.dataudipi.com",
        "https://dev-adm-ui.dataudipi.com",
        "http://dev-ui.dataudipi.com",
        "https://dev-ui.dataudipi.com"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(menu.router)
app.include_router(orders.router)
app.include_router(table.router)
app.include_router(inventory.router)
app.include_router(restaurants.router)
app.include_router(reports.router)
app.include_router(mcp_router)
