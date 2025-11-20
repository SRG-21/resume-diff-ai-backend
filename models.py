"""
Pydantic models for request/response validation
"""
from pydantic import BaseModel, Field, field_validator
from typing import List, Optional


class HighlightItem(BaseModel):
    """Individual highlight with term and context"""
    term: str = Field(..., description="The matched term/skill")
    context: str = Field(..., description="Context excerpt showing the occurrence")


class Highlights(BaseModel):
    """Optional highlights for JD and Resume matches"""
    jdMatches: Optional[List[HighlightItem]] = Field(None, description="Matches found in JD")
    resumeMatches: Optional[List[HighlightItem]] = Field(None, description="Matches found in Resume")


class CompareResponseModel(BaseModel):
    """Response model matching frontend contract exactly"""
    matchPercent: int = Field(..., ge=0, le=100, description="Match percentage (0-100)")
    matchedSkills: List[str] = Field(..., description="Array of matched skills")
    missingSkills: List[str] = Field(..., description="Array of missing skills")
    highlights: Optional[Highlights] = Field(None, description="Optional highlights")
    warnings: Optional[List[str]] = Field(None, description="Warnings or notices")
    
    @field_validator('matchPercent')
    @classmethod
    def validate_match_percent(cls, v):
        """Ensure matchPercent is between 0 and 100"""
        return max(0, min(100, v))


class HealthResponse(BaseModel):
    """Health check response"""
    status: str = Field(..., description="Status of the service")
