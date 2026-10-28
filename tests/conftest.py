"""
Pytest configuration and fixtures
"""
import os

# Settings are read at import time, so configure the environment first
os.environ.update({
    "ENVIRONMENT": "development",
    "JWT_SECRET_KEY": "test-secret-key-for-unit-tests-only",
    "OPENAI_API_KEY": "test-openai-key",
    "STRIPE_SECRET_KEY": "sk_test_dummy",
    "STRIPE_WEBHOOK_SECRET": "whsec_test_secret",
    "STRIPE_PRICE_PRO_MONTHLY": "price_pro_m",
    "STRIPE_PRICE_PRO_YEARLY": "price_pro_y",
    "STRIPE_PRICE_PASS": "price_pass",
    "STRIPE_PRICE_COACH_MONTHLY": "price_coach",
    "GOOGLE_CLIENT_ID": "test-google-client",
    "FRONTEND_URL": "http://localhost:5173",
    "EMAIL_BACKEND": "console",
})

import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from mongomock_motor import AsyncMongoMockClient

import database
from models import Breakdown, CompareResponseModel


@pytest_asyncio.fixture
async def db():
    """In-memory MongoDB with the production indexes"""
    client = AsyncMongoMockClient()
    database._client = client
    database._database = client["resume_diff_test"]
    await database._create_indexes()
    yield database._database
    database._database = None
    database._client = None


@pytest_asyncio.fixture
async def client(db):
    from main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def sent_emails(monkeypatch):
    """Capture outgoing emails as (to, subject, text)"""
    box = []

    async def fake_send(to, subject, text, html):
        box.append((to, subject, text))

    monkeypatch.setattr("email_service.send_email", fake_send)
    return box


@pytest_asyncio.fixture
async def make_user(client, sent_emails):
    """Create a user through the API, optionally on a paid plan. Returns (headers, user_id)."""
    counter = {"n": 0}

    async def _make(plan: str = "free", email: str = None):
        from datetime import datetime, timedelta
        counter["n"] += 1
        email = email or f"user{counter['n']}@example.com"
        resp = await client.post("/api/auth/signup", json={"email": email, "name": "Test User", "password": "Passw0rdOk"})
        assert resp.status_code == 201, resp.text
        body = resp.json()
        if plan != "free":
            fields = {"plan": plan, "subscription_status": "active", "plan_expires_at": datetime.utcnow() + timedelta(days=30)}
            await database.get_users_collection().update_one({"_id": body["user"]["id"]}, {"$set": fields})
        return {"Authorization": f"Bearer {body['access_token']}"}, body["user"]["id"]

    return _make


def fake_compare_result(**overrides) -> CompareResponseModel:
    data = dict(
        matchPercent=72,
        matchedSkills=["Python", "Docker"],
        missingSkills=["AWS", "Kubernetes"],
        jobTitle="Backend Engineer",
        company="Acme",
        breakdown=Breakdown(hardSkills=70, softSkills=60, experience=80, education=50, titleMatch=90, keywords=65),
    )
    data.update(overrides)
    return CompareResponseModel(**data)


@pytest.fixture
def mock_ai(monkeypatch):
    """Replace every OpenAI-backed function used by the routes"""
    mocks = {
        "compare": AsyncMock(return_value=fake_compare_result()),
        "rewrites": AsyncMock(return_value={
            "rewrites": [{"original": "Worked on Python apps", "suggested": "Built Python services handling 1M requests/day", "reason": "Quantified", "keywords": ["Python"]}],
            "gaps": [{"skill": "AWS", "advice": "Deploy a side project to Lambda"}],
        }),
        "cover": AsyncMock(return_value="Dear [Hiring Manager], ..."),
        "prep": AsyncMock(return_value=[{"question": "Tell me about Docker", "why": "Core skill", "answer_outline": "...", "category": "technical"}]),
    }
    monkeypatch.setattr("compare_routes.call_openai_completions", mocks["compare"])
    monkeypatch.setattr("ai_routes.call_openai_completions", mocks["compare"])
    monkeypatch.setattr("ai_routes.generate_rewrites", mocks["rewrites"])
    monkeypatch.setattr("ai_routes.generate_cover_letter", mocks["cover"])
    monkeypatch.setattr("ai_routes.generate_interview_prep", mocks["prep"])
    return mocks


@pytest.fixture
def mock_openai_response():
    """Load mock OpenAI response from JSON file"""
    mock_file = Path(__file__).parent / "mock_openai_response.json"
    with open(mock_file, 'r') as f:
        return json.load(f)


@pytest.fixture
def sample_jd_text():
    """Sample job description text"""
    return """
    We are looking for a Backend Engineer with the following skills:
    - Python (3+ years)
    - FastAPI framework
    - PostgreSQL database
    - Docker containerization
    - Redis caching
    - AWS cloud services
    - RESTful API design
    - Git version control
    """


@pytest.fixture
def sample_resume_text():
    """Sample resume text"""
    return """
    John Doe
    Backend Developer
    
    Skills:
    - Python (5 years experience)
    - Django framework
    - PostgreSQL
    - Docker
    - Redis
    - MySQL
    - Git
    
    Experience:
    Worked on various Python-based web applications using Django and PostgreSQL.
    Implemented caching with Redis and containerized applications using Docker.
    """


@pytest.fixture
def sample_pdf_content():
    """Create a minimal valid PDF for testing"""
    # Minimal PDF structure
    pdf = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>
endobj
4 0 obj
<< /Length 44 >>
stream
BT /F1 12 Tf 100 700 Td (Test Resume) Tj ET
endstream
endobj
5 0 obj
<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>
endobj
xref
0 6
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
0000000262 00000 n 
0000000355 00000 n 
trailer
<< /Size 6 /Root 1 0 R >>
startxref
443
%%EOF"""
    return pdf


@pytest.fixture
def sample_txt_content():
    """Sample text file content"""
    return b"This is a sample resume text file.\nPython developer with 5 years experience."
