"""ATS heuristics and SSRF-safe job import"""
import pytest

import httpx
import job_import
from ats_check import redact_for_plan, run_ats_check

REAL_CLIENT = httpx.AsyncClient

GOOD = """Jane Doe
jane@example.com | +1 (555) 123-4567

Summary
Backend engineer with six years building APIs.

Experience
- Built a payments API in Python serving 1M requests a day (2020 - 2024)
- Led migration to Docker and Kubernetes (2018 - 2020)
- Mentored four junior engineers and ran weekly design reviews
- Reduced infrastructure cost by 30 percent through autoscaling

Education
BSc Computer Science, 2014 - 2018

Skills
Python, FastAPI, Docker, PostgreSQL, AWS, Redis, Git, CI/CD, Terraform, Kubernetes, Linux, REST, GraphQL, Kafka, Celery, Pytest, SQL, Bash
""" + ("Additional project description with relevant detail and outcomes. " * 15)


def titles(report):
    return [i["title"] for i in report["issues"]]


def test_clean_resume_scores_high():
    r = run_ats_check(GOOD, "docx", {"tables": 0, "images": 0, "columns": 1})
    assert r["score"] >= 90, r["issues"]
    assert "Email address found" in r["passed"]


def test_missing_contact_and_sections_are_flagged():
    r = run_ats_check("I like programming. " * 40, "txt")
    t = titles(r)
    assert "No email address found" in t and "No phone number found" in t
    assert "No clear 'Experience' heading" in t
    assert r["score"] < 60


def test_layout_hazards_detected():
    r = run_ats_check(GOOD, "docx", {"tables": 2, "images": 1, "columns": 2, "header_footer_text": True})
    t = titles(r)
    assert {"Tables detected", "Images or graphics detected", "Multi-column layout", "Text in header or footer"} <= set(t)


def test_image_only_pdf_flagged():
    r = run_ats_check("Jane Doe jane@example.com " * 3, "pdf", {"pages": 2})
    assert "Text may be embedded as images" in titles(r)


def test_redaction_hides_issue_details_for_free_plan():
    full = run_ats_check("short", "txt")
    hidden = redact_for_plan(full, show_details=False)
    assert hidden["issues"] == [] and hidden["locked"] and hidden["issue_count"] == len(full["issues"])
    assert hidden["score"] == full["score"]
    assert redact_for_plan(full, True)["issues"] == full["issues"]


def test_parse_json_ld_job_posting():
    page = """<html><head><title>Careers</title><script type="application/ld+json">
    {"@context":"https://schema.org","@type":"JobPosting","title":"Data Engineer",
     "hiringOrganization":{"@type":"Organization","name":"Globex"},
     "description":"&lt;p&gt;Build pipelines with &lt;b&gt;Spark&lt;/b&gt;.&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Python&lt;/li&gt;&lt;/ul&gt;"}
    </script></head><body>cookie banner</body></html>"""
    out = job_import.parse_job_page(page)
    assert out["title"] == "Data Engineer" and out["company"] == "Globex"
    assert "Build pipelines with Spark." in out["text"] and "- Python" in out["text"]


def test_parse_plain_html_strips_scripts_and_nav():
    page = "<html><title>Role</title><nav>MENU</nav><script>var x=1</script><body><h1>Engineer</h1><p>Do <b>things</b></p></body></html>"
    out = job_import.parse_job_page(page)
    assert "MENU" not in out["text"] and "var x" not in out["text"]
    assert "Engineer" in out["text"] and "Do things" in out["text"]
    assert out["title"] == "Role"


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/admin",
    "http://localhost/",
    "http://10.0.0.5/x",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/",
    "http://192.168.1.1/",
    "file:///etc/passwd",
    "ftp://example.com/x",
    "https://example.com:8443/x",
    "not a url",
])
async def test_ssrf_urls_rejected(url):
    with pytest.raises(ValueError):
        await job_import.validate_public_url(url)


async def test_public_ip_literal_allowed():
    assert await job_import.validate_public_url("https://8.8.8.8/job") == "https://8.8.8.8/job"


async def test_redirect_to_private_address_is_blocked(monkeypatch):
    def handler(request: httpx.Request):
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(job_import.httpx, "AsyncClient", lambda **kw: REAL_CLIENT(transport=transport, **kw))
    with pytest.raises(ValueError):
        await job_import.fetch_job_text("https://8.8.8.8/job")


async def test_fetch_job_text_success_and_blocked_sites(monkeypatch):
    body = "<html><body>" + "<p>We are hiring a backend engineer to build and run APIs in Python.</p>" * 6 + "</body></html>"
    transport = httpx.MockTransport(lambda r: httpx.Response(200, text=body, headers={"content-type": "text/html"}))
    monkeypatch.setattr(job_import.httpx, "AsyncClient", lambda **kw: REAL_CLIENT(transport=transport, **kw))
    out = await job_import.fetch_job_text("https://8.8.8.8/job")
    assert "backend engineer" in out["text"]

    empty = httpx.MockTransport(lambda r: httpx.Response(200, text="<html>Sign in</html>", headers={"content-type": "text/html"}))
    monkeypatch.setattr(job_import.httpx, "AsyncClient", lambda **kw: REAL_CLIENT(transport=empty, **kw))
    monkeypatch.setattr(job_import, "validate_public_url", lambda u: _async(u))
    with pytest.raises(ValueError, match="require sign-in"):
        await job_import.fetch_job_text("https://www.linkedin.com/jobs/view/1")


async def _async(value):
    return value
