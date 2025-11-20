"""
OpenAI API integration for resume comparison using completions endpoint
"""
import json
import logging
import re
from typing import Dict, Any, Optional
import httpx

from config import settings
from models import CompareResponseModel

logger = logging.getLogger(__name__)


# Exact prompt template as specified
COMPARISON_PROMPT_TEMPLATE = """You are a strict JSON generator. Compare the following Job Description (JD) text and Resume text, and output a single JSON object with exactly these keys: matchPercent, matchedSkills, missingSkills, highlights (optional), warnings (optional). The JSON must be the only content in your response. matchPercent must be an integer from 0 to 100. matchedSkills and missingSkills must be arrays of strings. highlights (optional) can include jdMatches and resumeMatches each being arrays of objects with term and context.

JD text (start):

{{JD_TEXT}}

Resume text (start):

{{RESUME_TEXT}}

Instructions:

1. Identify skill tokens and role requirements from JD.
2. Find which JD skills/requirements are present in the resume (matched), and which are not (missing).
3. Compute matchPercent as round(100 * matched / (matched + missing)). If missing + matched = 0, set matchPercent to 0.
4. Provide matchedSkills (deduplicated), and missingSkills (deduplicated).
5. Optionally provide highlights.jdMatches and highlights.resumeMatches where each highlight object contains term and a short context excerpt showing the occurrence.
6. If you had to truncate text, or if there is ambiguity, include an entry in warnings.

Return EXACTLY one JSON object and nothing else.

Example output format:
{
  "matchPercent": 80,
  "matchedSkills": ["Python", "PostgreSQL", "Docker", "Redis"],
  "missingSkills": ["FastAPI", "AWS"],
  "highlights": {
    "jdMatches": [{"term":"Python","context":"We need a Backend Engineer experienced with Python"}],
    "resumeMatches": [{"term":"Python","context":"Worked on Python, Django,..."}]
  }
}"""


def build_comparison_prompt(jd_text: str, resume_text: str) -> str:
    """
    Build the comparison prompt with JD and resume text
    
    Args:
        jd_text: Job description text
        resume_text: Resume text
    
    Returns:
        Formatted prompt string
    """
    # Ensure texts are not too long for the model
    max_jd_length = 15000
    max_resume_length = 15000
    
    jd_truncated = False
    resume_truncated = False
    
    if len(jd_text) > max_jd_length:
        jd_text = jd_text[:max_jd_length]
        jd_truncated = True
    
    if len(resume_text) > max_resume_length:
        resume_text = resume_text[:max_resume_length]
        resume_truncated = True
    
    prompt = COMPARISON_PROMPT_TEMPLATE.replace("{{JD_TEXT}}", jd_text)
    prompt = prompt.replace("{{RESUME_TEXT}}", resume_text)
    
    if jd_truncated or resume_truncated:
        truncation_note = "\n\nNote: "
        if jd_truncated:
            truncation_note += "JD text was truncated. "
        if resume_truncated:
            truncation_note += "Resume text was truncated. "
        truncation_note += "Please include this in warnings."
        prompt += truncation_note
    
    return prompt


def extract_json_from_text(text: str) -> Optional[Dict[str, Any]]:
    """
    Try to extract JSON from model response using various heuristics
    
    Args:
        text: Raw model response
    
    Returns:
        Parsed JSON dict or None
    """
    # Try direct parsing first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    
    # Try to find JSON object in text
    json_pattern = r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}'
    matches = re.findall(json_pattern, text, re.DOTALL)
    
    for match in matches:
        try:
            return json.loads(match)
        except json.JSONDecodeError:
            continue
    
    # Try to extract content between first { and last }
    first_brace = text.find('{')
    last_brace = text.rfind('}')
    
    if first_brace != -1 and last_brace != -1 and first_brace < last_brace:
        try:
            return json.loads(text[first_brace:last_brace + 1])
        except json.JSONDecodeError:
            pass
    
    return None


def validate_and_coerce_response(data: Dict[str, Any]) -> CompareResponseModel:
    """
    Validate and coerce model response to match schema
    
    Args:
        data: Parsed JSON from model
    
    Returns:
        Validated CompareResponseModel
    
    Raises:
        ValueError: If data cannot be coerced to valid response
    """
    # Extract and validate matchedSkills and missingSkills
    matched_skills = data.get('matchedSkills', [])
    missing_skills = data.get('missingSkills', [])
    
    if not isinstance(matched_skills, list):
        matched_skills = []
    if not isinstance(missing_skills, list):
        missing_skills = []
    
    # Ensure all items are strings and deduplicate
    matched_skills = list(set(str(s) for s in matched_skills if s))
    missing_skills = list(set(str(s) for s in missing_skills if s))
    
    # Get or compute matchPercent
    match_percent = data.get('matchPercent')
    
    if match_percent is None or not isinstance(match_percent, (int, float)):
        # Try to compute from skills
        total = len(matched_skills) + len(missing_skills)
        if total > 0:
            match_percent = round(100 * len(matched_skills) / total)
        else:
            match_percent = 0
    
    match_percent = int(match_percent)
    match_percent = max(0, min(100, match_percent))
    
    # Process highlights if present
    highlights = None
    if 'highlights' in data and isinstance(data['highlights'], dict):
        highlights_data = data['highlights']
        jd_matches = highlights_data.get('jdMatches', [])
        resume_matches = highlights_data.get('resumeMatches', [])
        
        if isinstance(jd_matches, list) or isinstance(resume_matches, list):
            from models import Highlights, HighlightItem
            
            # Validate and limit highlights
            def validate_highlights(items):
                validated = []
                for item in items[:10]:  # Limit to 10 items
                    if isinstance(item, dict) and 'term' in item and 'context' in item:
                        validated.append(HighlightItem(
                            term=str(item['term'])[:100],  # Limit term length
                            context=str(item['context'])[:500]  # Limit context length
                        ))
                return validated if validated else None
            
            validated_jd = validate_highlights(jd_matches) if isinstance(jd_matches, list) else None
            validated_resume = validate_highlights(resume_matches) if isinstance(resume_matches, list) else None
            
            if validated_jd or validated_resume:
                highlights = Highlights(
                    jdMatches=validated_jd,
                    resumeMatches=validated_resume
                )
    
    # Process warnings
    warnings = data.get('warnings', [])
    if not isinstance(warnings, list):
        warnings = []
    warnings = [str(w)[:500] for w in warnings if w][:5]  # Limit to 5 warnings
    
    return CompareResponseModel(
        matchPercent=match_percent,
        matchedSkills=matched_skills,
        missingSkills=missing_skills,
        highlights=highlights,
        warnings=warnings if warnings else None
    )


async def call_openai_completions(
    jd_text: str,
    resume_text: str
) -> CompareResponseModel:
    """
    Call OpenAI completions API to compare JD and resume
    
    Args:
        jd_text: Job description text
        resume_text: Resume text
    
    Returns:
        CompareResponseModel with comparison results
    
    Raises:
        Exception: If API call fails or response is invalid
    """
    if not settings.OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY is not configured")
    
    prompt = build_comparison_prompt(jd_text, resume_text)
    
    request_payload = {
        "model": settings.OPENAI_MODEL,
        "prompt": prompt,
        "temperature": 0,
        "max_tokens": settings.OPENAI_MAX_TOKENS,
    }
    
    headers = {
        "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    
    logger.info(f"Calling OpenAI completions API with model: {settings.OPENAI_MODEL}")
    
    try:
        async with httpx.AsyncClient(timeout=settings.OPENAI_TIMEOUT) as client:
            response = await client.post(
                "https://api.openai.com/v1/completions",
                json=request_payload,
                headers=headers
            )
            response.raise_for_status()
            response_data = response.json()
    
    except httpx.TimeoutException as e:
        logger.error(f"OpenAI API timeout: {e}")
        raise Exception("OpenAI API request timed out")
    
    except httpx.HTTPStatusError as e:
        logger.error(f"OpenAI API HTTP error: {e.response.status_code} - {e.response.text}")
        raise Exception(f"OpenAI API error: {e.response.status_code}")
    
    except Exception as e:
        logger.error(f"OpenAI API call failed: {e}")
        raise Exception(f"Failed to call OpenAI API: {str(e)}")
    
    # Extract the completion text
    try:
        choices = response_data.get('choices', [])
        if not choices:
            raise ValueError("No choices in OpenAI response")
        
        completion_text = choices[0].get('text', '').strip()
        
        if not completion_text:
            raise ValueError("Empty completion text from OpenAI")
        
        logger.info(f"Received completion: {completion_text[:200]}...")
    
    except Exception as e:
        logger.error(f"Failed to extract completion text: {e}")
        raise Exception("Invalid response structure from OpenAI")
    
    # Parse JSON from completion
    parsed_json = extract_json_from_text(completion_text)
    
    if parsed_json is None:
        logger.error(f"Failed to parse JSON from completion: {completion_text[:500]}")
        # Return a safe fallback response with error warning
        return CompareResponseModel(
            matchPercent=0,
            matchedSkills=[],
            missingSkills=[],
            highlights=None,
            warnings=[
                "Model returned invalid JSON response",
                f"Response preview: {completion_text[:200]}..."
            ]
        )
    
    # Validate and coerce to response model
    try:
        result = validate_and_coerce_response(parsed_json)
        logger.info(f"Successfully generated comparison: {result.matchPercent}% match")
        return result
    
    except Exception as e:
        logger.error(f"Failed to validate response: {e}")
        raise Exception(f"Failed to validate model response: {str(e)}")
