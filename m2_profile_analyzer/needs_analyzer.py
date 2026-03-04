"""
Needs Analyzer Expert
Analyze Twitter profile bio and tweets to identify pain points and map to Dronor categories.

MKT-12: https://linear.app/extella/issue/MKT-12
"""
import requests
import json
import re

CATEGORIES = [
    "automation_builder",
    "ai_researcher",
    "solopreneur",
    "dev_productivity",
    "content_creator",
    "data_analyst",
    "startup_founder",
    "enterprise_ops",
    "other"
]

DRONOR_USE_CASES = [
    "workflow_automation",
    "data_processing",
    "content_generation",
    "research_analysis",
    "code_assistance",
    "task_scheduling",
    "api_integration",
    "document_processing",
    "social_media_automation",
    "email_automation"
]


def needs_analyzer(
    profile: dict,
    tweets: list,
    anthropic_api_key: str
) -> dict:
    """
    Analyze profile to identify needs and map to Dronor categories.
    
    9 Dronor categories:
    1. automation_builder - builds automations/workflows
    2. ai_researcher - studies AI/LLM for practical use
    3. solopreneur - solo founder, needs time savings
    4. dev_productivity - developer, wants faster workflow
    5. content_creator - creates content, needs automation
    6. data_analyst - works with data, needs AI tools
    7. startup_founder - building product, needs infrastructure
    8. enterprise_ops - corporate processes
    9. other - doesn't fit categories
    
    Args:
        profile: dict with profile data (bio, username, etc.)
        tweets: list of recent tweets
        anthropic_api_key: Claude API key
    
    Returns:
        dict with category, identified_needs[], dronor_use_cases[], confidence
    """
    if not anthropic_api_key:
        return {"status": "error", "message": "anthropic_api_key is required"}
    
    # Build prompt
    prompt = f"""Analyze this Twitter profile to identify their automation needs and pain points.

PROFILE DATA:
- Username: {profile.get('username', 'N/A')}
- Bio: {profile.get('bio', 'N/A')}
- Professional Role: {profile.get('professional_role', 'N/A')}
- Industry: {profile.get('industry', 'N/A')}
- Tech Stack: {profile.get('tech_stack', [])}
- Topics of Interest: {profile.get('topics_of_interest', [])}

RECENT TWEETS:
{json.dumps(tweets, indent=2)}

DRONOR CATEGORIES (choose 1 primary, optionally 1 secondary):
1. automation_builder - actively builds automations/workflows
2. ai_researcher - studies AI/LLM for practical applications
3. solopreneur - solo founder who needs time-saving tools
4. dev_productivity - developer wanting to speed up workflow
5. content_creator - creates content, needs automation help
6. data_analyst - works with data, needs AI tools
7. startup_founder - building product, needs infrastructure
8. enterprise_ops - corporate process optimization
9. other - doesn't fit above categories

DRONOR USE CASES (select 2-4 most relevant):
- workflow_automation: automating repetitive tasks
- data_processing: ETL, data transformation
- content_generation: AI-assisted writing/media
- research_analysis: market research, competitive analysis
- code_assistance: code generation, debugging
- task_scheduling: automated scheduling, reminders
- api_integration: connecting services via APIs
- document_processing: PDF, spreadsheet automation
- social_media_automation: posting, engagement
- email_automation: outreach, follow-ups

Analyze their pain points based on bio and tweets. What problems could Dronor solve for them?

Respond ONLY with valid JSON (no markdown):
{{
    "primary_category": "category from list",
    "secondary_category": "category or null",
    "identified_needs": [
        {{"need": "specific pain point", "context": "evidence from profile/tweets", "urgency": "high|medium|low"}}
    ],
    "dronor_use_cases": ["use_case_1", "use_case_2"],
    "confidence": 0.0-1.0,
    "reasoning": "brief explanation"
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
                "max_tokens": 800,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=45
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
            analysis = json.loads(content)
            
            # Validate and normalize categories
            primary = analysis.get("primary_category", "other")
            if primary not in CATEGORIES:
                primary_lower = primary.lower().replace(" ", "_").replace("-", "_")
                for cat in CATEGORIES:
                    if cat in primary_lower or primary_lower in cat:
                        primary = cat
                        break
                else:
                    primary = "other"
            
            secondary = analysis.get("secondary_category")
            if secondary and secondary not in CATEGORIES:
                secondary_lower = secondary.lower().replace(" ", "_").replace("-", "_")
                for cat in CATEGORIES:
                    if cat in secondary_lower or secondary_lower in cat:
                        secondary = cat
                        break
                else:
                    secondary = None
            
            # Validate use cases
            use_cases = analysis.get("dronor_use_cases", [])
            validated_use_cases = [uc for uc in use_cases if uc in DRONOR_USE_CASES]
            if not validated_use_cases:
                validated_use_cases = ["workflow_automation"]
            
            # Validate identified_needs format
            needs = analysis.get("identified_needs", [])
            validated_needs = []
            for need in needs:
                if isinstance(need, dict) and "need" in need:
                    validated_needs.append({
                        "need": need.get("need", ""),
                        "context": need.get("context", ""),
                        "tweet_url": need.get("tweet_url", ""),
                        "urgency": need.get("urgency", "medium") if need.get("urgency") in ["high", "medium", "low"] else "medium"
                    })
            
            return {
                "status": "success",
                "primary_category": primary,
                "secondary_category": secondary,
                "identified_needs": validated_needs,
                "dronor_use_cases": validated_use_cases,
                "confidence": float(analysis.get("confidence", 0.7)),
                "reasoning": analysis.get("reasoning", ""),
                "profile_username": profile.get("username", "unknown")
            }
        except json.JSONDecodeError:
            return {
                "status": "partial",
                "primary_category": "other",
                "secondary_category": None,
                "identified_needs": [],
                "dronor_use_cases": ["workflow_automation"],
                "confidence": 0.3,
                "reasoning": "Failed to parse LLM response",
                "raw_response": content[:500]
            }
            
    except requests.exceptions.Timeout:
        return {"status": "error", "message": "Claude API timeout"}
    except requests.exceptions.RequestException as e:
        return {"status": "error", "message": f"Request failed: {str(e)}"}
