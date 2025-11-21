"""
Integration tests for FastAPI endpoints
"""
import pytest
import io
from unittest.mock import patch, AsyncMock
from httpx import AsyncClient
from fastapi import UploadFile

from main import app
from models import CompareResponseModel


@pytest.mark.asyncio
async def test_health_endpoint():
    """Test health check endpoint"""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/health")
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_root_endpoint():
    """Test root endpoint"""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/")
    
    assert response.status_code == 200
    data = response.json()
    assert "message" in data
    assert "endpoints" in data


@pytest.mark.asyncio
async def test_compare_with_jd_text_and_resume_file(
    mock_openai_response,
    sample_jd_text,
    sample_txt_content
):
    """Test comparison with jd_text and resume_file"""
    
    # Mock the OpenAI API call
    with patch('openai_client.httpx.AsyncClient') as mock_client:
        mock_response = AsyncMock()
        mock_response.json.return_value = mock_openai_response
        mock_response.raise_for_status = AsyncMock()
        
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=mock_response
        )
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            files = {
                "resume_file": ("resume.txt", sample_txt_content, "text/plain")
            }
            data = {
                "jd_text": sample_jd_text
            }
            
            response = await client.post("/api/compare", files=files, data=data)
        
        assert response.status_code == 200
        result = response.json()
        
        assert "matchPercent" in result
        assert "matchedSkills" in result
        assert "missingSkills" in result
        assert isinstance(result["matchPercent"], int)
        assert 0 <= result["matchPercent"] <= 100


@pytest.mark.asyncio
async def test_compare_with_jd_file_and_resume_file(
    mock_openai_response,
    sample_txt_content
):
    """Test comparison with jd_file and resume_file"""
    
    with patch('openai_client.httpx.AsyncClient') as mock_client:
        mock_response = AsyncMock()
        mock_response.json.return_value = mock_openai_response
        mock_response.raise_for_status = AsyncMock()
        
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=mock_response
        )
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            files = {
                "jd_file": ("jd.txt", b"Python, FastAPI, AWS required", "text/plain"),
                "resume_file": ("resume.txt", sample_txt_content, "text/plain")
            }
            
            response = await client.post("/api/compare", files=files)
        
        assert response.status_code == 200
        result = response.json()
        assert "matchPercent" in result


@pytest.mark.asyncio
async def test_compare_with_both_jd_file_and_text(
    mock_openai_response,
    sample_jd_text,
    sample_txt_content
):
    """Test comparison with both jd_file and jd_text (should prefer file)"""
    
    with patch('openai_client.httpx.AsyncClient') as mock_client:
        mock_response = AsyncMock()
        mock_response.json.return_value = mock_openai_response
        mock_response.raise_for_status = AsyncMock()
        
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=mock_response
        )
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            files = {
                "jd_file": ("jd.txt", b"Python, FastAPI required", "text/plain"),
                "resume_file": ("resume.txt", sample_txt_content, "text/plain")
            }
            data = {
                "jd_text": sample_jd_text
            }
            
            response = await client.post("/api/compare", files=files, data=data)
        
        assert response.status_code == 200
        result = response.json()
        
        # Should have a warning about both being provided
        if result.get("warnings"):
            assert any("both" in w.lower() for w in result["warnings"])


@pytest.mark.asyncio
async def test_compare_missing_resume_file():
    """Test that missing resume_file returns 400"""
    
    async with AsyncClient(app=app, base_url="http://test") as client:
        data = {
            "jd_text": "Python required"
        }
        
        response = await client.post("/api/compare", data=data)
    
    assert response.status_code == 422  # FastAPI validation error


@pytest.mark.asyncio
async def test_compare_missing_jd():
    """Test that missing both jd_file and jd_text returns 400"""
    
    async with AsyncClient(app=app, base_url="http://test") as client:
        files = {
            "resume_file": ("resume.txt", b"Python developer", "text/plain")
        }
        
        response = await client.post("/api/compare", files=files)
    
    assert response.status_code == 400
    data = response.json()
    assert "jd_file or jd_text" in data["detail"]


@pytest.mark.asyncio
async def test_compare_invalid_file_type():
    """Test that invalid file type returns 400"""
    
    async with AsyncClient(app=app, base_url="http://test") as client:
        files = {
            "resume_file": ("resume.jpg", b"fake image", "image/jpeg")
        }
        data = {
            "jd_text": "Python required"
        }
        
        response = await client.post("/api/compare", files=files, data=data)
    
    assert response.status_code == 400 or response.status_code == 500
    # Should mention unsupported type


@pytest.mark.asyncio
async def test_compare_file_too_large(sample_jd_text):
    """Test that oversized file returns 400"""
    
    # Create a file larger than MAX_FILE_SIZE
    large_content = b"A" * (11 * 1024 * 1024)  # 11 MB
    
    async with AsyncClient(app=app, base_url="http://test") as client:
        files = {
            "resume_file": ("resume.txt", large_content, "text/plain")
        }
        data = {
            "jd_text": sample_jd_text
        }
        
        response = await client.post("/api/compare", files=files, data=data)
    
    assert response.status_code == 400
    data = response.json()
    assert "size" in data["detail"].lower() or "exceed" in data["detail"].lower()


@pytest.mark.asyncio
async def test_compare_invalid_model_response(sample_jd_text, sample_txt_content):
    """Test handling of invalid JSON from model"""
    
    # Mock invalid response
    invalid_response = {
        "choices": [
            {
                "text": "This is not valid JSON at all!",
                "index": 0,
                "finish_reason": "stop"
            }
        ]
    }
    
    with patch('openai_client.httpx.AsyncClient') as mock_client:
        mock_response = AsyncMock()
        mock_response.json.return_value = invalid_response
        mock_response.raise_for_status = AsyncMock()
        
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=mock_response
        )
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            files = {
                "resume_file": ("resume.txt", sample_txt_content, "text/plain")
            }
            data = {
                "jd_text": sample_jd_text
            }
            
            response = await client.post("/api/compare", files=files, data=data)
        
        # Should return a fallback response with warnings
        assert response.status_code == 200
        result = response.json()
        assert "warnings" in result
        assert any("invalid" in w.lower() for w in result["warnings"])


@pytest.mark.asyncio
async def test_compare_openai_timeout(sample_jd_text, sample_txt_content):
    """Test handling of OpenAI timeout"""
    
    import httpx
    
    with patch('openai_client.httpx.AsyncClient') as mock_client:
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(
            side_effect=httpx.TimeoutException("Request timed out")
        )
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            files = {
                "resume_file": ("resume.txt", sample_txt_content, "text/plain")
            }
            data = {
                "jd_text": sample_jd_text
            }
            
            response = await client.post("/api/compare", files=files, data=data)
        
        assert response.status_code == 502


@pytest.mark.asyncio
async def test_compare_pdf_file(mock_openai_response, sample_jd_text, sample_pdf_content):
    """Test comparison with PDF resume file"""
    
    with patch('openai_client.httpx.AsyncClient') as mock_client:
        mock_response = AsyncMock()
        mock_response.json.return_value = mock_openai_response
        mock_response.raise_for_status = AsyncMock()
        
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=mock_response
        )
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            files = {
                "resume_file": ("resume.pdf", sample_pdf_content, "application/pdf")
            }
            data = {
                "jd_text": sample_jd_text
            }
            
            response = await client.post("/api/compare", files=files, data=data)
        
        assert response.status_code == 200
        result = response.json()
        assert "matchPercent" in result
