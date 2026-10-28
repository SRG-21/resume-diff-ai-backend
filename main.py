"""
Resume Diff AI Backend - Production FastAPI Application
Compares resumes against job descriptions using OpenAI completions API
"""
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config import settings
from models import HealthResponse
from auth_routes import router as auth_router
from billing import router as billing_router
from compare_routes import router as compare_router
from ai_routes import router as ai_router
from tracker_routes import router as tracker_router
from database import connect_to_mongodb, close_mongodb_connection

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager - handles startup and shutdown"""
    # Startup: Connect to MongoDB
    logger.info("Starting up Resume Diff AI API...")
    await connect_to_mongodb()
    
    yield
    
    # Shutdown: Close MongoDB connection
    logger.info("Shutting down Resume Diff AI API...")
    await close_mongodb_connection()


# Initialize FastAPI app
app = FastAPI(
    title="Resume Diff AI API",
    description="API for resume analysis and comparison using OpenAI",
    version="2.0.0",
    lifespan=lifespan
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(auth_router)
app.include_router(billing_router)
app.include_router(compare_router)
app.include_router(ai_router)
app.include_router(tracker_router)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log all requests with unique ID"""
    request_id = str(uuid.uuid4())
    # Path only: query strings can carry one-time tokens
    logger.info(f"[{request_id}] {request.method} {request.url.path}")
    
    response = await call_next(request)
    
    logger.info(f"[{request_id}] Response status: {response.status_code}")
    return response


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "message": "Welcome to Resume Diff AI API",
        "version": "2.0.0",
        "endpoints": {
            "health": "/health",
            "compare": "/api/compare",
            "docs": "/docs"
        }
    }


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """
    Health check endpoint
    
    Returns:
        HealthResponse with status "ok"
    """
    return HealthResponse(status="ok")


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler for unhandled errors"""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error occurred"}
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=True
    )
