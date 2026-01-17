"""
Tender AI Platform - FastAPI Entry Point
Run with: uvicorn main:app --reload --port 8000
"""

import sys
import asyncio

# Windows fix: Playwright requires ProactorEventLoop for subprocess support
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from app.core.config import settings
from app.core.database import init_db
from app.api.routes import router

from app.core.config import settings
from app.core.database import init_db
from app.api.routes import router

# Configure logging
logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>",
    level="INFO"
)

# Create FastAPI app
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="AI-powered Moroccan Government Tender Analysis Platform",
    docs_url="/docs",
    redoc_url="/redoc"
)

# CORS middleware for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routes
app.include_router(router)


@app.get("/")
async def root():
    """Root endpoint - redirects to API docs"""
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "status": "running",
        "docs": "/docs",
        "health": "/health"
    }


@app.on_event("startup")
async def startup():
    """Initialize on startup"""
    logger.info("=" * 60)
    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")
    logger.info("=" * 60)
    
    # Initialize database
    logger.info("Initializing database...")
    init_db()
    logger.info("Database ready")
    
    # Check DeepSeek API key
    if settings.DEEPSEEK_API_KEY:
        logger.info("DeepSeek API key configured ✓")
    else:
        logger.warning("DeepSeek API key NOT configured - AI features disabled")
    
    logger.info(f"Server ready at http://localhost:8000")
    logger.info(f"API docs at http://localhost:8000/docs")


@app.on_event("shutdown")
async def shutdown():
    """Cleanup on shutdown"""
    logger.info("Shutting down...")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.DEBUG
    )
