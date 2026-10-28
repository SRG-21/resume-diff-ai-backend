"""
Resume vs job description comparison, saved history and saved resume versions.
"""
import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status

from ats_check import redact_for_plan, run_ats_check
from config import settings
from database import get_comparisons_collection, get_resumes_collection
from deps import get_active_user, upgrade_required
from file_utils import ExtractedDocument, extract_document
from job_import import fetch_job_text
from models import Breakdown, CompareResponseModel
from openai_client import AIServiceError, call_openai_completions
from plans import Plan, get_plan
from usage import SCANS, consume, enforce_rate_limit, refund

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Compare"])

STORED_TEXT_LIMIT = 30000


# ============ Helpers ============

def shape_result(doc: dict, plan: Plan) -> CompareResponseModel:
    """Build the API response from a stored comparison, hiding what the plan does not include"""
    locked = []
    breakdown = None
    if plan.breakdown and doc.get("breakdown"):
        breakdown = Breakdown(**doc["breakdown"])
    elif not plan.breakdown:
        locked.append("breakdown")

    ats = None
    if doc.get("ats"):
        ats = redact_for_plan(doc["ats"], plan.ats_details)
        if not plan.ats_details:
            locked.append("ats_details")

    return CompareResponseModel(
        id=doc["_id"],
        matchPercent=doc["match_percent"],
        matchedSkills=doc.get("matched_skills", []),
        missingSkills=doc.get("missing_skills", []),
        highlights=doc.get("highlights"),
        warnings=doc.get("warnings"),
        jobTitle=doc.get("job_title"),
        company=doc.get("company"),
        breakdown=breakdown,
        ats=ats,
        locked=locked,
    )


async def _read_jd(jd_file: Optional[UploadFile], jd_text: Optional[str], warnings: list) -> str:
    """Resolve the job description from a file or pasted text (file wins; text is the fallback)"""
    text = (jd_text or "").strip()
    if jd_file and jd_file.filename:
        try:
            doc = await extract_document(jd_file)
            if doc.warning:
                warnings.append(doc.warning)
            if text:
                warnings.append("Both jd_file and jd_text provided; using jd_file content")
            return doc.text
        except ValueError as e:
            if text:
                warnings.append(f"JD file extraction failed ({e}), using jd_text instead")
                return text
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(f"JD file extraction failed: {e}")
            if text:
                warnings.append("JD file extraction error, using jd_text instead")
                return text
            raise HTTPException(status_code=400, detail="Could not read the job description file.")
    return text


async def _load_saved_resume(user_id: str, resume_id: str) -> dict:
    doc = await get_resumes_collection().find_one({"_id": resume_id, "user_id": user_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Saved resume not found")
    return doc


async def save_resume_record(user: dict, name: str, doc: ExtractedDocument) -> dict:
    """Store a resume version if the plan has room; 402 otherwise"""
    plan = get_plan(user)
    coll = get_resumes_collection()
    if await coll.count_documents({"user_id": user["id"]}) >= plan.saved_resumes:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "code": "resume_limit",
                "message": f"Your {plan.name} plan keeps up to {plan.saved_resumes} saved resume(s). Delete one or upgrade.",
                "plan": plan.id,
                "limit": plan.saved_resumes,
            },
        )
    record = {
        "_id": str(uuid.uuid4()),
        "user_id": user["id"],
        "name": (name or doc.filename)[:120],
        "filename": doc.filename,
        "file_type": doc.file_type,
        "meta": doc.meta,
        "text": doc.text[:STORED_TEXT_LIMIT],
        "words": len(doc.text.split()),
        "created_at": datetime.utcnow(),
    }
    await coll.insert_one(record)
    return record


def _resume_summary(r: dict) -> dict:
    return {
        "id": r["_id"],
        "name": r["name"],
        "filename": r.get("filename"),
        "file_type": r.get("file_type"),
        "words": r.get("words", 0),
        "created_at": r["created_at"].isoformat(),
    }


# ============ Compare ============

@router.post("/api/compare", response_model=CompareResponseModel)
async def compare(
    jd_file: Optional[UploadFile] = File(None),
    jd_text: Optional[str] = Form(None),
    jd_url: Optional[str] = Form(None),
    resume_file: Optional[UploadFile] = File(None),
    resume_id: Optional[str] = Form(None),
    save_resume_as: Optional[str] = Form(None),
    current_user: dict = Depends(get_active_user),
):
    """
    Compare a resume against a job description (requires authentication).

    The resume comes from `resume_file` or a previously saved `resume_id`; the job
    description from `jd_file`, `jd_text`, or (paid plans) a public `jd_url`.
    """
    request_id = str(uuid.uuid4())
    logger.info(f"[{request_id}] Starting comparison for user {current_user['id']}")
    await enforce_rate_limit(f"compare:{current_user['id']}", 10, 60, "You are comparing too quickly. Please wait a minute.")
    plan = get_plan(current_user)
    warnings: list = []

    # --- resume ---
    resume_doc: Optional[ExtractedDocument] = None
    if resume_file and resume_file.filename:
        try:
            resume_doc = await extract_document(resume_file)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(f"[{request_id}] Resume extraction failed: {e}")
            raise HTTPException(status_code=400, detail="Could not read the resume file. Try a different PDF or DOCX.")
        if resume_doc.warning:
            warnings.append(resume_doc.warning)
    elif resume_id:
        saved = await _load_saved_resume(current_user["id"], resume_id)
        resume_doc = ExtractedDocument(
            text=saved["text"], warning=None, filename=saved.get("filename") or saved["name"],
            file_type=saved.get("file_type", "txt"), meta=saved.get("meta", {}),
        )
    else:
        raise HTTPException(status_code=400, detail="resume_file or resume_id is required")

    # --- job description ---
    jd_source = "text"
    job_title = job_company = None
    jd_final = await _read_jd(jd_file, jd_text, warnings)
    if jd_file and jd_file.filename and jd_final:
        jd_source = "file"
    if not jd_final and jd_url:
        if not plan.job_url_import:
            raise upgrade_required("job_url_import", plan.id)
        try:
            imported = await fetch_job_text(jd_url)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        jd_final, job_title, job_company, jd_source = imported["text"], imported.get("title"), imported.get("company"), "url"
    if not jd_final:
        raise HTTPException(status_code=400, detail="Provide a job description as a file, text or link")
    if len(jd_final.strip()) < 10:
        raise HTTPException(status_code=400, detail="The job description is too short (minimum 10 characters)")

    # --- quota (after validation so bad input does not use a scan) ---
    usage = await consume(current_user, SCANS)

    try:
        result = await call_openai_completions(jd_final, resume_doc.text)
    except ValueError as e:
        await refund(current_user, SCANS)
        logger.error(f"[{request_id}] Configuration error: {e}")
        raise HTTPException(status_code=500, detail="The service is not configured correctly. Please contact support.")
    except AIServiceError as e:
        await refund(current_user, SCANS)
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        await refund(current_user, SCANS)
        logger.error(f"[{request_id}] Unexpected AI error: {e}", exc_info=True)
        raise HTTPException(status_code=502, detail="The AI service is temporarily unavailable. Please try again.")

    if result.warnings:
        warnings = result.warnings + warnings
    # A comparison with no skills means the model output was unusable: do not charge for it
    if not result.matchedSkills and not result.missingSkills and result.matchPercent == 0:
        await refund(current_user, SCANS)
        usage = {"used": max(0, usage["used"] - 1), "limit": usage["limit"]}

    ats_report = run_ats_check(resume_doc.text, resume_doc.file_type, resume_doc.meta)

    now = datetime.utcnow()
    stored = {
        "_id": str(uuid.uuid4()),
        "user_id": current_user["id"],
        "created_at": now,
        "expires_at": now + timedelta(days=settings.HISTORY_RETENTION_DAYS),
        "job_title": result.jobTitle or (job_title or None),
        "company": result.company or job_company,
        "jd_source": jd_source,
        "jd_url": jd_url if jd_source == "url" else None,
        "resume_name": (save_resume_as or resume_doc.filename)[:120],
        "resume_id": resume_id,
        "match_percent": result.matchPercent,
        "matched_skills": result.matchedSkills,
        "missing_skills": result.missingSkills,
        "highlights": result.highlights.model_dump() if result.highlights else None,
        "breakdown": result.breakdown.model_dump() if result.breakdown else None,
        "warnings": warnings or None,
        "ats": ats_report,
        "jd_text": jd_final[:STORED_TEXT_LIMIT],
        "resume_text": resume_doc.text[:STORED_TEXT_LIMIT],
    }
    await get_comparisons_collection().insert_one(stored)

    if save_resume_as is not None and resume_file and resume_file.filename:
        try:
            saved = await save_resume_record(current_user, save_resume_as, resume_doc)
            await get_comparisons_collection().update_one({"_id": stored["_id"]}, {"$set": {"resume_id": saved["_id"]}})
        except HTTPException:
            warnings.append("Resume was not saved because you have reached your saved-resume limit.")
            stored["warnings"] = warnings

    response = shape_result(stored, plan)
    response.usage = usage
    response.warnings = warnings or None
    logger.info(f"[{request_id}] Comparison complete: {result.matchPercent}%")
    return response


@router.post("/api/import-job")
async def import_job(body: dict, user: dict = Depends(get_active_user)):
    """Fetch a public job posting so the user can review the text before comparing (paid plans)"""
    plan = get_plan(user)
    if not plan.job_url_import:
        raise upgrade_required("job_url_import", plan.id)
    await enforce_rate_limit(f"import:{user['id']}", 20, 60, "Too many imports. Please wait a minute.")
    url = str(body.get("url", "")).strip()
    if not url:
        raise HTTPException(status_code=400, detail="url is required")
    try:
        imported = await fetch_job_text(url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"text": imported["text"], "title": imported.get("title"), "company": imported.get("company")}


# ============ History ============

def _history_summary(d: dict) -> dict:
    return {
        "id": d["_id"],
        "job_title": d.get("job_title"),
        "company": d.get("company"),
        "resume_name": d.get("resume_name"),
        "match_percent": d["match_percent"],
        "ats_score": (d.get("ats") or {}).get("score"),
        "matched_count": len(d.get("matched_skills", [])),
        "missing_count": len(d.get("missing_skills", [])),
        "created_at": d["created_at"].isoformat(),
    }


@router.get("/api/history")
async def list_history(skip: int = 0, limit: int = 50, user: dict = Depends(get_active_user)):
    """Saved comparisons, newest first. Free plans can open only their latest few."""
    plan = get_plan(user)
    coll = get_comparisons_collection()
    total = await coll.count_documents({"user_id": user["id"]})
    cursor = coll.find({"user_id": user["id"]}, {"jd_text": 0, "resume_text": 0}).sort("created_at", -1).skip(max(0, skip)).limit(max(1, min(limit, 100)))
    items = [d async for d in cursor]
    summaries = []
    for index, d in enumerate(items, start=skip):
        item = _history_summary(d)
        item["locked"] = bool(plan.history_limit and index >= plan.history_limit)
        summaries.append(item)
    return {"items": summaries, "total": total, "history_limit": plan.history_limit}


async def load_comparison(user: dict, comparison_id: str, with_text: bool = False) -> dict:
    projection = None if with_text else {"jd_text": 0, "resume_text": 0}
    doc = await get_comparisons_collection().find_one({"_id": comparison_id, "user_id": user["id"]}, projection)
    if not doc:
        raise HTTPException(status_code=404, detail="Comparison not found")
    return doc


@router.get("/api/history/{comparison_id}", response_model=CompareResponseModel)
async def get_history_item(comparison_id: str, user: dict = Depends(get_active_user)):
    plan = get_plan(user)
    doc = await load_comparison(user, comparison_id)
    if plan.history_limit:
        newer = await get_comparisons_collection().count_documents(
            {"user_id": user["id"], "created_at": {"$gt": doc["created_at"]}}
        )
        if newer >= plan.history_limit:
            raise upgrade_required("history", plan.id)
    return shape_result(doc, plan)


@router.delete("/api/history/{comparison_id}")
async def delete_history_item(comparison_id: str, user: dict = Depends(get_active_user)):
    result = await get_comparisons_collection().delete_one({"_id": comparison_id, "user_id": user["id"]})
    if not result.deleted_count:
        raise HTTPException(status_code=404, detail="Comparison not found")
    return {"message": "Deleted"}


@router.delete("/api/history")
async def clear_history(user: dict = Depends(get_active_user)):
    result = await get_comparisons_collection().delete_many({"user_id": user["id"]})
    return {"deleted": result.deleted_count}


# ============ Saved resumes ============

@router.get("/api/resumes")
async def list_resumes(user: dict = Depends(get_active_user)):
    plan = get_plan(user)
    cursor = get_resumes_collection().find({"user_id": user["id"]}, {"text": 0}).sort("created_at", -1)
    return {"items": [_resume_summary(d) async for d in cursor], "limit": plan.saved_resumes}


@router.post("/api/resumes", status_code=status.HTTP_201_CREATED)
async def upload_resume(
    resume_file: UploadFile = File(...),
    name: Optional[str] = Form(None),
    user: dict = Depends(get_active_user),
):
    try:
        doc = await extract_document(resume_file)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Resume extraction failed: {e}")
        raise HTTPException(status_code=400, detail="Could not read the resume file. Try a different PDF or DOCX.")
    record = await save_resume_record(user, name or "", doc)
    return _resume_summary(record)


@router.delete("/api/resumes/{resume_id}")
async def delete_resume(resume_id: str, user: dict = Depends(get_active_user)):
    result = await get_resumes_collection().delete_one({"_id": resume_id, "user_id": user["id"]})
    if not result.deleted_count:
        raise HTTPException(status_code=404, detail="Resume not found")
    return {"message": "Deleted"}
