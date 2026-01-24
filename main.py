"""
AI Working Search - Backend API
FastAPI application for job search with AI agents
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from config import get_settings
from api.routes import chat
from api.routes import vacancies
from api.routes import scheduler as scheduler_routes
from api.routes import employer_moderation
from api.routes import agents_admin
from scheduler import start_scheduler, shutdown_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle events"""
    # Startup
    print("🚀 Starting AI Working Search API...")
    settings = get_settings()
    print(f"   Model: {settings.model_name}")
    print(f"   Debug: {settings.debug}")

    # Start scheduler
    start_scheduler()
    print("📅 Scheduler started")

    yield

    # Shutdown
    shutdown_scheduler()
    print("👋 Shutting down...")


app = FastAPI(
    title="AI Working Search API",
    description="Поиск работы с помощью AI-агентов",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS - разрешаем все origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes (AI поиск, scheduler, модерация вакансий работодателей, управление агентами)
app.include_router(chat.router, prefix="/api")
app.include_router(vacancies.router, prefix="/api/vacancies")
app.include_router(scheduler_routes.router)
app.include_router(employer_moderation.router)
app.include_router(agents_admin.router)


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
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug,
    )
