"""
Category Detector Expert - M2 Profile Analyzer
Classifies profiles into 9 Dronor use case categories.

MKT-11: Instance-B
"""
import requests
import json
import re
import os


CATEGORIES = [
    "Skill Enablement",
    "Product Building",
    "Operations Automation",
    "Data Processing",
    "Content Creation",
    "Research & Analysis",
    "Customer Operations",
    "Finance & Admin",
    "HR & Recruiting"
]


def category_detector(
    profile: dict,
    tweets: list = None,
    anthropic_api_key: str = None
) -> dict:
    """
    Classify profile into one of 9 Dronor use case categories.
    
    Categories:
    1. Skill Enablement - Learning, upskilling, education
    2. Product Building - Development, engineering, PM
    3. Operations Automation - Workflows, processes
    4. Data Processing - Analytics, ETL, pipelines
    5. Content Creation - Marketing, social, writing
    6. Research & Analysis - Market/competitive research
    7. Customer Operations - Support, success, CRM
    8. Finance & Admin - Accounting, invoicing
    9. HR & Recruiting - Hiring, onboarding
    
    Returns:
        primary_category, secondary_category, confidence, evidence[]
    """
    if tweets is None:
        tweets = []
    
    if not anthropic_api_key:
        anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not anthropic_api_key:
        try:
            with open("/containers_folder/credentials/anthropic_key.txt") as f:
                anthropic_api_key = f.read().strip()
        except FileNotFoundError:
            return {"status": "error", "message": "anthropic_api_key required"}
    
    prompt = f"""Analyze this Twitter profile for Dronor use case categories.

PROFILE:
- Username: {profile.get('username', 'N/A')}
- Bio: {profile.get('bio', 'N/A')}
- Role: {profile.get('professional_role', 'N/A')}
- Industry: {profile.get('industry', 'N/A')}
- Tech Stack: {profile.get('tech_stack', [])}
- Topics: {profile.get('topics_of_interest', [])}

TWEETS:
{json.dumps(tweets, indent=2)}

CATEGORIES:
1. Skill Enablement - Learning, upskilling
2. Product Building - Development, engineering
3. Operations Automation - Workflows, processes
4. Data Processing - Analytics, ETL
5. Content Creation - Marketing, writing
6. Research & Analysis - Market research
7. Customer Operations - Support, CRM
8. Finance & Admin - Accounting, admin
9. HR & Recruiting - Hiring, people ops

Return JSON:
{{"primary_category": "name", "secondary_category": "name or null", "confidence": 0.0-1.0, "evidence": ["reason1", "reason2"]}}"""

    try:
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "Content-Type": "application/json",
                "x-api-key": anthropic_api_key,
                "anthropic-version": "2023-06-01"
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 400,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=30
        )
        
        if response.status_code != 200:
            return {"status": "error", "message": f"API error: {response.status_code}"}
        
        content = response.json()["content"][0]["text"].strip()
        if content.startswith("```"):
            match = re.search(r'```(?:json)?\s*(.*?)\s*```', content, re.DOTALL)
            if match:
                content = match.group(1).strip()
        
        result = json.loads(content)
        
        def normalize_category(cat):
            if not cat:
                return None
            for valid in CATEGORIES:
                if valid.lower() in cat.lower() or cat.lower() in valid.lower():
                    return valid
            return cat
        
        return {
            "status": "success",
            "primary_category": normalize_category(result.get("primary_category")),
            "secondary_category": normalize_category(result.get("secondary_category")),
            "confidence": float(result.get("confidence", 0.7)),
            "evidence": result.get("evidence", []),
            "profile_username": profile.get("username", "unknown")
        }
        
    except Exception as e:
        return {"status": "error", "message": str(e)}


if __name__ == "__main__":
    test_profile = {
        "username": "john_founder",
        "bio": "Founder @SaaSCo | Building automation tools",
        "professional_role": "founder",
        "industry": "SaaS"
    }
    print(category_detector(test_profile))
