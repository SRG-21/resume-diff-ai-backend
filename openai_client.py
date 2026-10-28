"""
OpenAI API integration for resume comparison using completions endpoint
"""
import json
import logging
import re
from typing import Dict, Any, Optional
import httpx

from config import settings
from models import Breakdown, CompareResponseModel

logger = logging.getLogger(__name__)


class AIServiceError(Exception):
    """Upstream AI failure. The message is safe to show to users; details are logged."""


# Exact prompt template as specified
COMPARISON_PROMPT_TEMPLATE = """You are a strict JSON generator. Compare the following Job Description (JD) text and Resume text, and output a single JSON object with exactly these keys: matchPercent, matchedSkills, missingSkills, jobTitle, company, breakdown, highlights (optional), warnings (optional). The JSON must be the only content in your response. matchPercent must be an integer from 0 to 100. matchedSkills and missingSkills must be arrays of strings. jobTitle and company are strings taken from the JD (empty string if unknown). breakdown is an object with integer scores from 0 to 100 for hardSkills, softSkills, experience, education, titleMatch and keywords. highlights (optional) can include jdMatches and resumeMatches each being arrays of objects with term and context.

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
  "jobTitle": "Backend Engineer",
  "company": "Acme Corp",
  "breakdown": {"hardSkills": 75, "softSkills": 60, "experience": 80, "education": 70, "titleMatch": 90, "keywords": 66},
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
    # Reduce text lengths to speed up OpenAI response (avoid API Gateway 29s timeout)
    max_jd_length = 6000
    max_resume_length = 6000
    
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
    matched_skills = list(dict.fromkeys(str(s).strip() for s in matched_skills if s and str(s).strip()))
    missing_skills = list(dict.fromkeys(str(s).strip() for s in missing_skills if s and str(s).strip()))
    
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
    
    breakdown = None
    raw_breakdown = data.get('breakdown')
    if isinstance(raw_breakdown, dict):
        def _score(key):
            try:
                return max(0, min(100, int(float(raw_breakdown.get(key, 0)))))
            except (TypeError, ValueError):
                return 0
        breakdown = Breakdown(**{k: _score(k) for k in Breakdown.model_fields})

    def _text(key):
        value = data.get(key)
        return str(value).strip()[:200] if isinstance(value, str) and value.strip() else None

    return CompareResponseModel(
        matchPercent=match_percent,
        matchedSkills=matched_skills,
        missingSkills=missing_skills,
        highlights=highlights,
        warnings=warnings if warnings else None,
        jobTitle=_text('jobTitle'),
        company=_text('company'),
        breakdown=breakdown,
    )


async def chat_completion(
    system: str,
    user: str,
    max_tokens: Optional[int] = None,
    temperature: float = 0,
    json_mode: bool = True,
) -> str:
    """
    Single Chat Completions call. Returns the message text.

    Raises:
        ValueError: if OPENAI_API_KEY is missing
        AIServiceError: for upstream failures (message is user-safe)
    """
    if not settings.OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY is not configured")

    request_payload = {
        "model": settings.OPENAI_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens or settings.OPENAI_MAX_TOKENS,
    }
    if json_mode:
        request_payload["response_format"] = {"type": "json_object"}

    headers = {
        "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    logger.info(f"Calling OpenAI Chat Completions API with model: {settings.OPENAI_MODEL}")

    try:
        async with httpx.AsyncClient(timeout=settings.OPENAI_TIMEOUT) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                json=request_payload,
                headers=headers
            )
            response.raise_for_status()
            response_data = response.json()

    except httpx.TimeoutException as e:
        logger.error(f"OpenAI API timeout: {e}")
        raise AIServiceError("The AI service took too long to respond. Please try again.")

    except httpx.HTTPStatusError as e:
        logger.error(f"OpenAI API HTTP error: {e.response.status_code} - {e.response.text}")
        raise AIServiceError("The AI service is temporarily unavailable. Please try again.")

    except Exception as e:
        logger.error(f"OpenAI API call failed: {e}")
        raise AIServiceError("The AI service is temporarily unavailable. Please try again.")

    try:
        choices = response_data.get('choices', [])
        if not choices:
            raise ValueError("No choices in OpenAI response")
        completion_text = (choices[0].get('message', {}) or {}).get('content', '').strip()
        if not completion_text:
            raise ValueError("Empty completion text from OpenAI")
        logger.info(f"Received completion: {completion_text[:200]}...")
        return completion_text
    except Exception as e:
        logger.error(f"Failed to extract completion text: {e}")
        raise AIServiceError("The AI service returned an unexpected response. Please try again.")


async def chat_json(system: str, user: str, max_tokens: Optional[int] = None, temperature: float = 0.3) -> Dict[str, Any]:
    """Chat call that must return a JSON object; raises AIServiceError otherwise"""
    text = await chat_completion(system, user, max_tokens=max_tokens, temperature=temperature)
    parsed = extract_json_from_text(text)
    if not isinstance(parsed, dict):
        logger.error(f"Could not parse JSON from completion: {text[:500]}")
        raise AIServiceError("The AI service returned an unexpected response. Please try again.")
    return parsed


async def call_openai_completions(
    jd_text: str,
    resume_text: str
) -> CompareResponseModel:
    """
    Call OpenAI to compare JD and resume
    
    Args:
        jd_text: Job description text
        resume_text: Resume text
    
    Returns:
        CompareResponseModel with comparison results
    
    Raises:
        AIServiceError: If the API call fails or the response is invalid
    """
    prompt = build_comparison_prompt(jd_text, resume_text)
    completion_text = await chat_completion(
        "You are a strict JSON generator. Output only valid JSON with no additional text or markdown.",
        prompt,
        temperature=0,
    )

    parsed_json = extract_json_from_text(completion_text)

    if parsed_json is None:
        logger.error(f"Failed to parse JSON from completion: {completion_text[:500]}")
        # Return a safe fallback response with error warning
        return CompareResponseModel(
            matchPercent=0,
            matchedSkills=[],
            missingSkills=[],
            highlights=None,
            warnings=["The AI response could not be read. Please try again."]
        )

    try:
        result = validate_and_coerce_response(parsed_json)
        logger.info(f"Successfully generated comparison: {result.matchPercent}% match")
        return result

    except Exception as e:
        logger.error(f"Failed to validate response: {e}")
        raise AIServiceError("The AI service returned an unexpected response. Please try again.")


# ============ Paid AI features ============

REWRITE_SYSTEM = (
    "You are an expert resume writer. Output only valid JSON. Never invent employers, titles, "
    "degrees, dates, metrics or technologies the candidate has not shown evidence of."
)


async def generate_rewrites(jd_text: str, resume_text: str, missing_skills: list) -> dict:
    """Suggest truthful bullet rewrites that work missing/weak keywords into existing experience"""
    user = (
        "Job description:\n" + jd_text[:5000] + "\n\nResume:\n" + resume_text[:5000] + "\n\n"
        "Skills the JD asks for that the resume lacks: " + ", ".join(missing_skills[:15]) + "\n\n"
        "Pick up to 8 existing resume lines (copy each EXACTLY, character for character) that can be improved "
        "for this job. For each, write a stronger version that is specific, uses an action verb, and includes "
        "a relevant keyword ONLY when the original line already supports it. If a skill has no support in the "
        "resume, do not claim it; list it under 'gaps' with advice on how to gain or show it.\n"
        'Return JSON: {"rewrites":[{"original":"...","suggested":"...","reason":"...","keywords":["..."]}],'
        '"gaps":[{"skill":"...","advice":"..."}]}'
    )
    data = await chat_json(REWRITE_SYSTEM, user, max_tokens=1800, temperature=0.4)
    rewrites = []
    for item in (data.get("rewrites") or [])[:8]:
        if isinstance(item, dict) and item.get("original") and item.get("suggested"):
            rewrites.append({
                "original": str(item["original"])[:600],
                "suggested": str(item["suggested"])[:600],
                "reason": str(item.get("reason", ""))[:300],
                "keywords": [str(k)[:60] for k in (item.get("keywords") or [])][:6] if isinstance(item.get("keywords"), list) else [],
            })
    gaps = []
    for item in (data.get("gaps") or [])[:8]:
        if isinstance(item, dict) and item.get("skill"):
            gaps.append({"skill": str(item["skill"])[:80], "advice": str(item.get("advice", ""))[:300]})
    return {"rewrites": rewrites, "gaps": gaps}


async def generate_cover_letter(jd_text: str, resume_text: str, tone: str, job_title: str, company: str) -> str:
    user = (
        f"Write a {tone} cover letter (250-350 words) for the role of {job_title or 'the advertised role'}"
        f"{' at ' + company if company else ''}.\n\nJob description:\n{jd_text[:5000]}\n\nCandidate resume:\n{resume_text[:5000]}\n\n"
        "Rules: use only facts present in the resume; address the strongest 2-3 requirements; no placeholders "
        "other than [Hiring Manager] and [Your Name]; no fluff openings.\n"
        'Return JSON: {"letter":"..."}'
    )
    data = await chat_json(REWRITE_SYSTEM, user, max_tokens=900, temperature=0.6)
    letter = str(data.get("letter", "")).strip()
    if not letter:
        raise AIServiceError("The AI service returned an unexpected response. Please try again.")
    return letter[:6000]


async def generate_interview_prep(jd_text: str, resume_text: str, missing_skills: list) -> list:
    user = (
        "Job description:\n" + jd_text[:5000] + "\n\nResume:\n" + resume_text[:5000] + "\n\n"
        "Skills the resume lacks: " + ", ".join(missing_skills[:15]) + "\n\n"
        "Write 8 likely interview questions for this role, weighted toward the skill gaps and the candidate's "
        "weakest areas. For each give the reason it will be asked and a short answer outline built from the "
        "candidate's real experience (or an honest way to address the gap).\n"
        'Return JSON: {"questions":[{"question":"...","why":"...","answer_outline":"...","category":"technical|behavioral|gap"}]}'
    )
    data = await chat_json(REWRITE_SYSTEM, user, max_tokens=1800, temperature=0.5)
    out = []
    for item in (data.get("questions") or [])[:10]:
        if isinstance(item, dict) and item.get("question"):
            out.append({
                "question": str(item["question"])[:300],
                "why": str(item.get("why", ""))[:300],
                "answer_outline": str(item.get("answer_outline", ""))[:600],
                "category": str(item.get("category", "technical"))[:20],
            })
    return out
