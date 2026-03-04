"""
Category Detector Expert
Classify Twitter profile into one of 9 Dronor use case categories.

MKT-11: https://linear.app/extella/issue/MKT-11
"""
import requests
import json
import re

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
    tweets: list,
    anthropic_api_key: str
) -> dict:
    """
    Classify profile into one of 9 Dronor use case categories.
    
    Categories:
    1. Skill Enablement - Learning, upskilling, education
    2. Product Building - Development, engineering, product management
    3. Operations Automation - Workflows, processes, efficiency
    4. Data Processing - Analytics, ETL, data pipelines
    5. Content Creation - Marketing, social media, writing
    6. Research & Analysis - Market research, competitive analysis
    7. Customer Operations - Support, success, CRM
    8. Finance & Admin - Accounting, invoicing, admin tasks
    9. HR & Recruiting - Hiring, onboarding, people ops
    
    Args:
        profile: dict with profile data
        tweets: list of recent tweets
        anthropic_api_key: Claude API key
    
    Returns:
        dict with primary_category, secondary_category, confidence, evidence[]
    """
    if not anthropic_api_key:
        return {"status": "error", "message": "anthropic_api_key is required"}
    
    # Build prompt
    prompt = f"""Analyze this Twitter profile and classify them into the most relevant Dronor use case categories.

PROFILE DATA:
- Username: {profile.get('username', 'N/A')}
- Bio: {profile.get('bio', 'N/A')}
- Professional Role: {profile.get('professional_role', 'N/A')}
- Industry: {profile.get('industry', 'N/A')}
- Tech Stack: {profile.get('tech_stack', [])}
- Topics of Interest: {profile.get('topics_of_interest', [])}

RECENT TWEETS:
{json.dumps(tweets, indent=2)}

AVAILABLE CATEGORIES:
1. Skill Enablement - Learning, upskilling, education tools
2. Product Building - Development, engineering, product management
3. Operations Automation - Workflows, processes, efficiency
4. Data Processing - Analytics, ETL, data pipelines
5. Content Creation - Marketing, social media, writing
6. Research & Analysis - Market research, competitive analysis
7. Customer Operations - Support, success, CRM
8. Finance & Admin - Accounting, invoicing, admin tasks
9. HR & Recruiting - Hiring, onboarding, people ops

Classify this person based on what automation tools they would most likely need.

Respond ONLY with valid JSON (no markdown):
{{
    "primary_category": "exact category name from list",
    "secondary_category": "exact category name from list or null",
    "confidence": 0.0-1.0,
    "evidence": ["reason 1", "reason 2", "reason 3"]
}}"""

    # Call Claude API
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
            return {
                "status": "error",
                "message": f"Claude API error {response.status_code}: {response.text}"
            }
        
        result = response.json()
        content = result["content"][0]["text"].strip()
        
        # Remove markdown code blocks if present
        if content.startswith("```"):
            match = re.search(r'```(?:json)?\s*(.*?)\s*```', content, re.DOTALL)
            if match:
                content = match.group(1).strip()
        
        # Parse LLM response
        try:
            classification = json.loads(content)
            
            # Validate categories
            primary = classification.get("primary_category", "")
            secondary = classification.get("secondary_category")
            
            # Normalize category names (fuzzy match)
            def normalize_category(cat):
                if not cat:
                    return None
                cat_lower = cat.lower()
                for valid_cat in CATEGORIES:
                    if valid_cat.lower() in cat_lower or cat_lower in valid_cat.lower():
                        return valid_cat
                return cat  # Return as-is if no match
            
            return {
                "status": "success",
                "primary_category": normalize_category(primary),
                "secondary_category": normalize_category(secondary),
                "confidence": float(classification.get("confidence", 0.7)),
                "evidence": classification.get("evidence", []),
                "profile_username": profile.get("username", "unknown")
            }
        except json.JSONDecodeError:
            return {
                "status": "partial",
                "primary_category": "Operations Automation",
                "secondary_category": None,
                "confidence": 0.3,
                "evidence": ["Failed to parse LLM response"],
                "raw_response": content[:500]
            }
            
    except requests.exceptions.Timeout:
        return {"status": "error", "message": "Claude API timeout"}
    except requests.exceptions.RequestException as e:
        return {"status": "error", "message": f"Request failed: {str(e)}"}
