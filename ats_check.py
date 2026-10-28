"""
Deterministic ATS-friendliness check for a resume.

No LLM involved: it looks at the extracted text and at structural facts gathered
at upload time (tables, images, columns, page count), so it is free to run.
"""
import re
from typing import List, Optional

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(\+?\d[\d\s().-]{7,}\d)")
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
BULLET_RE = re.compile(r"^\s*([-*•▪●◦‣·]|\d+[.)])\s+", re.MULTILINE)
FANCY_RE = re.compile(r"[←-⇿☀-➿\U0001F000-\U0001FFFF-]")

SECTION_PATTERNS = {
    "experience": r"\b(work\s+)?experience\b|\bemployment\b|\bwork history\b|\bprofessional background\b",
    "education": r"\beducation\b|\bacademic\b",
    "skills": r"\bskills\b|\btechnical skills\b|\bcompetencies\b|\btechnologies\b",
    "summary": r"\bsummary\b|\bprofile\b|\bobjective\b|\babout me\b",
}

PENALTY = {"error": 15, "warning": 7, "info": 3}


def _issue(severity: str, title: str, detail: str) -> dict:
    return {"severity": severity, "title": title, "detail": detail}


def run_ats_check(text: str, file_type: str = "txt", meta: Optional[dict] = None) -> dict:
    """Return {"score": 0-100, "issues": [...], "passed": [...]}"""
    meta = meta or {}
    issues: List[dict] = []
    passed: List[str] = []
    lowered = text.lower()
    words = len(text.split())

    # Contact details
    if EMAIL_RE.search(text):
        passed.append("Email address found")
    else:
        issues.append(_issue("error", "No email address found", "Recruiters and ATS profiles need a plain-text email in the body of the resume, not only in a header or image."))
    if PHONE_RE.search(text):
        passed.append("Phone number found")
    else:
        issues.append(_issue("warning", "No phone number found", "Add a phone number so recruiters can reach you."))

    # Standard section headings
    for name, pattern in SECTION_PATTERNS.items():
        if re.search(pattern, lowered):
            passed.append(f"'{name.title()}' section found")
        elif name in ("experience", "education", "skills"):
            issues.append(_issue("warning", f"No clear '{name.title()}' heading", f"ATS parsers look for standard headings. Add a heading called '{name.title()}'."))

    # Length
    if words < 150:
        issues.append(_issue("error", "Resume is very short", f"Only {words} words were readable. Short resumes rarely contain enough keywords to rank."))
    elif words > 1300:
        issues.append(_issue("warning", "Resume is very long", f"{words} words. Aim for one to two pages (roughly 400-900 words)."))
    else:
        passed.append("Reasonable length")
    pages = meta.get("pages", 0)
    if pages > 2:
        issues.append(_issue("warning", f"Resume is {pages} pages", "Unless you have 10+ years of experience, keep it to two pages."))

    # Text extractability (scanned or image-only PDFs)
    if file_type == "pdf" and pages and words / pages < 80:
        issues.append(_issue("error", "Text may be embedded as images", "Very little text could be read from the PDF. If it was scanned or exported as pictures, an ATS will see a blank page. Export a text-based PDF or upload a DOCX."))

    # Layout hazards
    if meta.get("tables"):
        issues.append(_issue("warning", "Tables detected", "Many ATS read tables out of order or skip them. Use plain paragraphs and bullets."))
    if meta.get("images"):
        issues.append(_issue("warning", "Images or graphics detected", "Images, logos and skill-bar graphics are invisible to an ATS."))
    if meta.get("columns", 1) > 1:
        issues.append(_issue("warning", "Multi-column layout", "Columns can scramble reading order. A single column is the safest layout."))
    if meta.get("header_footer_text"):
        issues.append(_issue("info", "Text in header or footer", "Some ATS ignore headers and footers. Keep contact details in the main body."))
    if not any([meta.get("tables"), meta.get("images"), meta.get("columns", 1) > 1]) and file_type in ("docx", "pdf"):
        passed.append("Simple single-column layout")

    # Bullets and dates
    if "experience" in lowered and len(BULLET_RE.findall(text)) < 3:
        issues.append(_issue("warning", "Few bullet points", "Describe achievements as short bullet points; they are easier for ATS and recruiters to scan."))
    elif len(BULLET_RE.findall(text)) >= 3:
        passed.append("Uses bullet points")
    if len(YEAR_RE.findall(text)) < 2:
        issues.append(_issue("warning", "Few dates found", "Include start and end dates for roles and education so the ATS can build your timeline."))
    else:
        passed.append("Dates present")

    # Characters
    if FANCY_RE.search(text):
        issues.append(_issue("info", "Special symbols or emoji", "Decorative symbols can turn into garbage characters. Use standard bullets."))
    if file_type == "txt":
        issues.append(_issue("info", "Plain text upload", "Formatting cannot be checked for .txt files. Most ATS prefer DOCX or a text-based PDF."))

    score = max(0, 100 - sum(PENALTY[i["severity"]] for i in issues))
    return {"score": score, "issues": issues, "passed": passed}


def redact_for_plan(report: dict, show_details: bool) -> dict:
    """Free users see the score and how many problems exist, not what they are."""
    if show_details:
        return {**report, "locked": False}
    return {
        "score": report["score"],
        "issues": [],
        "passed": [],
        "issue_count": len(report["issues"]),
        "locked": True,
    }
