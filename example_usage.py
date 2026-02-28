"""
Example usage of the Resume Diff AI API
Demonstrates different ways to call the /api/compare endpoint
"""
import requests
import json


# Configuration
API_BASE_URL = "http://localhost:8000"


def check_health():
    """Check if the API is running"""
    response = requests.get(f"{API_BASE_URL}/health")
    print(f"Health Check: {response.json()}")
    return response.status_code == 200


def compare_with_text_jd(jd_text: str, resume_file_path: str):
    """
    Compare using JD text and resume file
    
    Args:
        jd_text: Job description as text string
        resume_file_path: Path to resume file (PDF/DOCX/TXT)
    """
    print("\n=== Comparing with JD Text and Resume File ===")
    
    with open(resume_file_path, 'rb') as resume_file:
        files = {
            'resume_file': (resume_file_path, resume_file, 'application/pdf')
        }
        data = {
            'jd_text': jd_text
        }
        
        response = requests.post(
            f"{API_BASE_URL}/api/compare",
            files=files,
            data=data
        )
    
    if response.status_code == 200:
        result = response.json()
        print(f"Match Percentage: {result['matchPercent']}%")
        print(f"Matched Skills: {', '.join(result['matchedSkills'])}")
        print(f"Missing Skills: {', '.join(result['missingSkills'])}")
        
        if result.get('warnings'):
            print(f"Warnings: {result['warnings']}")
        
        return result
    else:
        print(f"Error: {response.status_code} - {response.json()}")
        return None


def compare_with_file_jd(jd_file_path: str, resume_file_path: str):
    """
    Compare using JD file and resume file
    
    Args:
        jd_file_path: Path to JD file (PDF)
        resume_file_path: Path to resume file (PDF/DOCX/TXT)
    """
    print("\n=== Comparing with JD File and Resume File ===")
    
    with open(jd_file_path, 'rb') as jd_file, \
         open(resume_file_path, 'rb') as resume_file:
        
        files = {
            'jd_file': (jd_file_path, jd_file, 'application/pdf'),
            'resume_file': (resume_file_path, resume_file, 'application/pdf')
        }
        
        response = requests.post(
            f"{API_BASE_URL}/api/compare",
            files=files
        )
    
    if response.status_code == 200:
        result = response.json()
        print(f"Match Percentage: {result['matchPercent']}%")
        print(f"Matched Skills ({len(result['matchedSkills'])}): {result['matchedSkills']}")
        print(f"Missing Skills ({len(result['missingSkills'])}): {result['missingSkills']}")
        
        # Print highlights if available
        if result.get('highlights'):
            if result['highlights'].get('jdMatches'):
                print("\nJD Highlights:")
                for match in result['highlights']['jdMatches'][:3]:  # First 3
                    print(f"  - {match['term']}: {match['context'][:80]}...")
            
            if result['highlights'].get('resumeMatches'):
                print("\nResume Highlights:")
                for match in result['highlights']['resumeMatches'][:3]:  # First 3
                    print(f"  - {match['term']}: {match['context'][:80]}...")
        
        return result
    else:
        print(f"Error: {response.status_code} - {response.json()}")
        return None


def save_result_to_file(result: dict, output_path: str):
    """Save comparison result to JSON file"""
    with open(output_path, 'w') as f:
        json.dump(result, f, indent=2)
    print(f"\nResult saved to: {output_path}")


if __name__ == "__main__":
    # Check if API is running
    if not check_health():
        print("ERROR: API is not running. Please start the server first.")
        print("Run: uvicorn main:app --reload")
        exit(1)
    
    # Example 1: Using JD text
    sample_jd = """
    We are hiring a Senior Backend Engineer with the following requirements:
    
    Required Skills:
    - Python (5+ years)
    - FastAPI or Django framework
    - PostgreSQL database design
    - Docker and Kubernetes
    - AWS (EC2, S3, Lambda)
    - RESTful API design
    - Git and CI/CD
    - Redis caching
    
    Nice to have:
    - GraphQL
    - Microservices architecture
    - Test-driven development
    """
    
    # Replace with your actual file path
    resume_path = "sample_resume.pdf"  # Make sure this file exists
    
    # Uncomment to run example
    # result = compare_with_text_jd(sample_jd, resume_path)
    # if result:
    #     save_result_to_file(result, "comparison_result.json")
    
    # Example 2: Using JD file
    jd_path = "job_description.pdf"  # Make sure this file exists
    
    # Uncomment to run example
    # result = compare_with_file_jd(jd_path, resume_path)
    
    print("\n" + "="*60)
    print("Examples are commented out. Update file paths and uncomment to run.")
    print("="*60)
