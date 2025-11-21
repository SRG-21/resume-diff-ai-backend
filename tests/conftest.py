"""
Pytest configuration and fixtures
"""
import pytest
import json
import os
from pathlib import Path


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
