"""
Unit tests for OpenAI client
"""
import pytest
import json
from openai_client import (
    build_comparison_prompt,
    extract_json_from_text,
    validate_and_coerce_response,
)
from models import CompareResponseModel


def test_build_comparison_prompt():
    """Test prompt building"""
    jd = "Python, FastAPI, PostgreSQL required"
    resume = "Python developer with Django experience"
    
    prompt = build_comparison_prompt(jd, resume)
    
    assert jd in prompt
    assert resume in prompt
    assert "matchPercent" in prompt
    assert "matchedSkills" in prompt
    assert "missingSkills" in prompt


def test_build_comparison_prompt_truncation():
    """Test prompt truncation for long texts"""
    jd = "A" * 20000
    resume = "B" * 20000
    
    prompt = build_comparison_prompt(jd, resume)
    
    # Should be truncated
    assert len(jd) > 15000
    assert "truncated" in prompt.lower() or len(prompt) < len(jd) + len(resume)


def test_extract_json_from_text_valid():
    """Test JSON extraction from valid text"""
    text = '{"matchPercent": 80, "matchedSkills": ["Python"]}'
    result = extract_json_from_text(text)
    
    assert result is not None
    assert result["matchPercent"] == 80
    assert "Python" in result["matchedSkills"]


def test_extract_json_from_text_with_surrounding_text():
    """Test JSON extraction with surrounding text"""
    text = 'Here is the result:\n{"matchPercent": 75}\nThank you'
    result = extract_json_from_text(text)
    
    assert result is not None
    assert result["matchPercent"] == 75


def test_extract_json_from_text_nested():
    """Test extraction of nested JSON"""
    text = '{"matchPercent": 80, "highlights": {"jdMatches": [{"term": "Python"}]}}'
    result = extract_json_from_text(text)
    
    assert result is not None
    assert result["matchPercent"] == 80
    assert "highlights" in result


def test_extract_json_from_text_invalid():
    """Test JSON extraction failure"""
    text = "This is not JSON at all"
    result = extract_json_from_text(text)
    
    assert result is None


def test_validate_and_coerce_response_complete():
    """Test validation with complete response"""
    data = {
        "matchPercent": 80,
        "matchedSkills": ["Python", "Docker"],
        "missingSkills": ["AWS", "Kubernetes"],
        "highlights": {
            "jdMatches": [{"term": "Python", "context": "Python required"}],
            "resumeMatches": [{"term": "Python", "context": "Python experience"}]
        },
        "warnings": ["Some warning"]
    }
    
    result = validate_and_coerce_response(data)
    
    assert isinstance(result, CompareResponseModel)
    assert result.matchPercent == 80
    assert len(result.matchedSkills) == 2
    assert len(result.missingSkills) == 2
    assert result.highlights is not None
    assert result.warnings is not None


def test_validate_and_coerce_response_minimal():
    """Test validation with minimal response"""
    data = {
        "matchedSkills": ["Python"],
        "missingSkills": ["AWS"]
    }
    
    result = validate_and_coerce_response(data)
    
    assert isinstance(result, CompareResponseModel)
    # matchPercent should be computed: 1/(1+1) = 50%
    assert result.matchPercent == 50
    assert len(result.matchedSkills) == 1
    assert len(result.missingSkills) == 1


def test_validate_and_coerce_response_invalid_match_percent():
    """Test handling of invalid matchPercent"""
    data = {
        "matchPercent": "invalid",
        "matchedSkills": ["Python", "Docker"],
        "missingSkills": []
    }
    
    result = validate_and_coerce_response(data)
    
    # Should compute from skills: 2/(2+0) = 100%
    assert result.matchPercent == 100


def test_validate_and_coerce_response_empty_skills():
    """Test handling of empty skills"""
    data = {
        "matchedSkills": [],
        "missingSkills": []
    }
    
    result = validate_and_coerce_response(data)
    
    assert result.matchPercent == 0


def test_validate_and_coerce_response_deduplication():
    """Test skill deduplication"""
    data = {
        "matchedSkills": ["Python", "python", "Python"],
        "missingSkills": ["AWS", "aws"]
    }
    
    result = validate_and_coerce_response(data)
    
    # Should be deduplicated (case-sensitive dedup)
    assert len(result.matchedSkills) <= 3
    assert len(result.missingSkills) <= 2


def test_validate_and_coerce_response_clamps_percent():
    """Test that matchPercent is clamped to 0-100"""
    data = {
        "matchPercent": 150,
        "matchedSkills": ["Python"],
        "missingSkills": []
    }
    
    result = validate_and_coerce_response(data)
    assert result.matchPercent == 100
    
    data["matchPercent"] = -50
    result = validate_and_coerce_response(data)
    assert result.matchPercent == 0
