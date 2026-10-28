"""
Application tracker: a simple board of jobs the user is pursuing.
"""
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from pymongo import ReturnDocument

from database import get_applications_collection, get_comparisons_collection
from deps import require_feature

router = APIRouter(prefix="/api/applications", tags=["Tracker"])

STATUSES = ("saved", "applied", "interview", "offer", "rejected")
STATUS_PATTERN = "^(" + "|".join(STATUSES) + ")$"


class ApplicationCreate(BaseModel):
    company: str = Field(..., min_length=1, max_length=120)
    role: str = Field(..., min_length=1, max_length=160)
    status: str = Field("saved", pattern=STATUS_PATTERN)
    url: Optional[str] = Field(None, max_length=500)
    notes: Optional[str] = Field(None, max_length=4000)
    comparison_id: Optional[str] = None
    applied_on: Optional[str] = Field(None, max_length=10)  # YYYY-MM-DD


class ApplicationUpdate(BaseModel):
    company: Optional[str] = Field(None, min_length=1, max_length=120)
    role: Optional[str] = Field(None, min_length=1, max_length=160)
    status: Optional[str] = Field(None, pattern=STATUS_PATTERN)
    url: Optional[str] = Field(None, max_length=500)
    notes: Optional[str] = Field(None, max_length=4000)
    applied_on: Optional[str] = Field(None, max_length=10)


def _out(d: dict) -> dict:
    return {
        "id": d["_id"],
        "company": d["company"],
        "role": d["role"],
        "status": d["status"],
        "url": d.get("url"),
        "notes": d.get("notes"),
        "comparison_id": d.get("comparison_id"),
        "match_percent": d.get("match_percent"),
        "applied_on": d.get("applied_on"),
        "created_at": d["created_at"].isoformat(),
        "updated_at": d["updated_at"].isoformat(),
    }


@router.get("")
async def list_applications(user: dict = Depends(require_feature("tracker"))):
    cursor = get_applications_collection().find({"user_id": user["id"]}).sort("updated_at", -1)
    return {"items": [_out(d) async for d in cursor], "statuses": list(STATUSES)}


@router.post("", status_code=201)
async def create_application(body: ApplicationCreate, user: dict = Depends(require_feature("tracker"))):
    match_percent = None
    if body.comparison_id:
        comparison = await get_comparisons_collection().find_one(
            {"_id": body.comparison_id, "user_id": user["id"]}, {"match_percent": 1}
        )
        if not comparison:
            raise HTTPException(status_code=404, detail="Comparison not found")
        match_percent = comparison["match_percent"]
    now = datetime.utcnow()
    doc = {
        "_id": str(uuid.uuid4()),
        "user_id": user["id"],
        **body.model_dump(),
        "match_percent": match_percent,
        "created_at": now,
        "updated_at": now,
    }
    await get_applications_collection().insert_one(doc)
    return _out(doc)


@router.patch("/{application_id}")
async def update_application(application_id: str, body: ApplicationUpdate, user: dict = Depends(require_feature("tracker"))):
    changes = body.model_dump(exclude_unset=True)
    changes["updated_at"] = datetime.utcnow()
    doc = await get_applications_collection().find_one_and_update(
        {"_id": application_id, "user_id": user["id"]}, {"$set": changes}, return_document=ReturnDocument.AFTER
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Application not found")
    return _out(doc)


@router.delete("/{application_id}")
async def delete_application(application_id: str, user: dict = Depends(require_feature("tracker"))):
    result = await get_applications_collection().delete_one({"_id": application_id, "user_id": user["id"]})
    if not result.deleted_count:
        raise HTTPException(status_code=404, detail="Application not found")
    return {"message": "Deleted"}
