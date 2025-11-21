"""
Unit tests for file extraction utilities
"""
import pytest
import io
from file_utils import (
    sanitize_text,
    extract_text_from_txt,
    extract_text_from_pdf,
)


def test_sanitize_text_basic():
    """Test basic text sanitization"""
    text = "  Hello   World  \n\n\n  Test  "
    clean, truncated = sanitize_text(text)
    assert "Hello World" in clean
    assert not truncated


def test_sanitize_text_removes_null_bytes():
    """Test that null bytes are removed"""
    text = "Hello\x00World"
    clean, truncated = sanitize_text(text)
    assert "\x00" not in clean
    assert "HelloWorld" in clean


def test_sanitize_text_truncation():
    """Test text truncation"""
    long_text = "A" * 200000
    clean, truncated = sanitize_text(long_text, max_length=1000)
    assert len(clean) == 1000
    assert truncated


def test_extract_text_from_txt_utf8():
    """Test UTF-8 text extraction"""
    content = "Hello World\nPython Developer".encode('utf-8')
    text = extract_text_from_txt(content, "test.txt")
    assert "Hello World" in text
    assert "Python Developer" in text


def test_extract_text_from_txt_latin1_fallback():
    """Test latin-1 fallback for text extraction"""
    content = "Café".encode('latin-1')
    text = extract_text_from_txt(content, "test.txt")
    assert len(text) > 0


def test_extract_text_from_pdf(sample_pdf_content):
    """Test PDF text extraction"""
    text = extract_text_from_pdf(sample_pdf_content, "test.pdf")
    # The minimal PDF should extract something
    assert isinstance(text, str)
