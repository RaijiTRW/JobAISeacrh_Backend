"""
AI Working Search - Backend API
FastAPI application for job search with AI agents
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.config import get_settings
from app.api.routes import chat


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle events"""
    # Startup
    print("🚀 Starting AI Working Search API...")
    settings = get_settings()
    print(f"   Model: {settings.model_name}")
    print(f"   Debug: {settings.debug}")
    yield
    # Shutdown
    print("👋 Shutting down...")


app = FastAPI(
    title="AI Working Search API",
    description="Поиск работы с помощью AI-агентов",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes
app.include_router(chat.router, prefix="/api")


@app.get("/")
async def root():
    return {
        "name": "AI Working Search API",
        "version": "1.0.0",
        "status": "running",
    }


@app.get("/health")
async def health():
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug,
    )
