"""
Resume Diff AI Backend - Production FastAPI Application
Compares resumes against job descriptions using OpenAI completions API
"""
import logging
import uuid
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config import settings
from models import CompareResponseModel, HealthResponse
from file_utils import extract_text_from_file
from openai_client import call_openai_completions

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="Resume Diff AI API",
    description="API for resume analysis and comparison using OpenAI",
    version="1.0.0"
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log all requests with unique ID"""
    request_id = str(uuid.uuid4())
    logger.info(f"[{request_id}] {request.method} {request.url.path}")
    
    response = await call_next(request)
    
    logger.info(f"[{request_id}] Response status: {response.status_code}")
    return response


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "message": "Welcome to Resume Diff AI API",
        "version": "1.0.0",
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


@app.post("/api/compare", response_model=CompareResponseModel)
async def compare(
    jd_file: Optional[UploadFile] = File(None),
    jd_text: Optional[str] = Form(None),
    resume_file: UploadFile = File(...),
):
    """
    Compare resume against job description
    
    Args:
        jd_file: Optional PDF file containing job description
        jd_text: Optional text string containing job description
        resume_file: Required file (PDF/DOCX/DOC/TXT) containing resume
    
    Returns:
        CompareResponseModel with match percentage, skills, and highlights
    
    Raises:
        HTTPException: 400 for validation errors, 500 for server errors, 502 for invalid model response
    """
    request_id = str(uuid.uuid4())
    logger.info(f"[{request_id}] Starting comparison request")
    
    warnings = []
    
    try:
        # Validation: resume_file must exist
        if not resume_file or not resume_file.filename:
            raise HTTPException(
                status_code=400,
                detail="resume_file is required"
            )
        
        # Validation: at least one JD source must be provided
        if not jd_file and not jd_text:
            raise HTTPException(
                status_code=400,
                detail="At least one of jd_file or jd_text must be provided"
            )
        
        # Extract resume text
        logger.info(f"[{request_id}] Extracting resume from {resume_file.filename}")
        try:
            resume_text, resume_warning = await extract_text_from_file(resume_file)
            if resume_warning:
                warnings.append(resume_warning)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(f"[{request_id}] Resume extraction failed: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to extract resume text: {str(e)}"
            )
        
        # Extract or use JD text
        jd_final_text = ""
        
        if jd_file and jd_file.filename:
            logger.info(f"[{request_id}] Extracting JD from file {jd_file.filename}")
            try:
                jd_file_text, jd_warning = await extract_text_from_file(jd_file)
                if jd_warning:
                    warnings.append(jd_warning)
                jd_final_text = jd_file_text
                
                # If both file and text provided, prefer file but note it
                if jd_text:
                    warnings.append("Both jd_file and jd_text provided; using jd_file content")
            
            except ValueError as e:
                # If file extraction fails but we have jd_text, use that
                if jd_text:
                    warnings.append(f"JD file extraction failed ({str(e)}), using jd_text instead")
                    jd_final_text = jd_text
                else:
                    raise HTTPException(status_code=400, detail=str(e))
            
            except Exception as e:
                logger.error(f"[{request_id}] JD file extraction failed: {e}")
                if jd_text:
                    warnings.append(f"JD file extraction error, using jd_text instead")
                    jd_final_text = jd_text
                else:
                    raise HTTPException(
                        status_code=500,
                        detail=f"Failed to extract JD text: {str(e)}"
                    )
        
        elif jd_text:
            jd_final_text = jd_text.strip()
            if len(jd_final_text) < 10:
                raise HTTPException(
                    status_code=400,
                    detail="jd_text is too short (minimum 10 characters)"
                )
        
        # Validate we have JD text
        if not jd_final_text or len(jd_final_text.strip()) < 10:
            raise HTTPException(
                status_code=400,
                detail="Job description text is too short or empty"
            )
        
        logger.info(
            f"[{request_id}] Texts extracted - JD: {len(jd_final_text)} chars, "
            f"Resume: {len(resume_text)} chars"
        )
        
        # Call OpenAI completions API
        logger.info(f"[{request_id}] Calling OpenAI for comparison")
        try:
            result = await call_openai_completions(jd_final_text, resume_text)
            
            # Add any accumulated warnings to the result
            if warnings:
                if result.warnings:
                    result.warnings.extend(warnings)
                else:
                    result.warnings = warnings
            
            logger.info(
                f"[{request_id}] Comparison complete - Match: {result.matchPercent}%, "
                f"Matched: {len(result.matchedSkills)}, Missing: {len(result.missingSkills)}"
            )
            
            return result
        
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        
        except Exception as e:
            logger.error(f"[{request_id}] OpenAI call failed: {e}")
            raise HTTPException(
                status_code=502,
                detail=f"OpenAI API error: {str(e)}"
            )
    
    except HTTPException:
        raise
    
    except Exception as e:
        logger.error(f"[{request_id}] Unexpected error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )


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
