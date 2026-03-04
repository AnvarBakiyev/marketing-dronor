"""
Wave Classifier Expert - M2 Profile Analyzer
Classifies Twitter profiles into Tier S/A/B/C/D for wave outreach strategy.

MKT-10: Instance-B
"""
import requests
import json
import re
import os


def wave_classifier(
    profile: dict,
    tweets: list = None,
    anthropic_api_key: str = None
) -> dict:
    """
    Classify profile into Tier S/A/B/C/D for wave outreach.
    
    Tier criteria:
    - S: 10K+ followers + strong automation signals
    - A: 1K-10K followers + clear professional role + automation interest
    - B: 500-1K followers + relevant industry
    - C: followers of competitors
    - D: long-tail keyword matches
    
    Args:
        profile: Profile dict with username, bio, followers_count, etc.
        tweets: List of recent tweets
        anthropic_api_key: Claude API key (or reads from env/file)
    
    Returns:
        dict with tier, confidence, reasoning
    """
    if tweets is None:
        tweets = []
    
    # Get API key
    if not anthropic_api_key:
        anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not anthropic_api_key:
        try:
            with open("/containers_folder/credentials/anthropic_key.txt") as f:
                anthropic_api_key = f.read().strip()
        except FileNotFoundError:
            return {"status": "error", "message": "anthropic_api_key required"}
    
    # Build prompt
    prompt = f"""Classify this Twitter profile into a tier for outreach about workflow automation tools.

PROFILE DATA:
- Username: {profile.get('username', 'N/A')}
- Bio: {profile.get('bio', 'N/A')}
- Followers: {profile.get('followers_count', 0)}
- Professional Role: {profile.get('professional_role', 'N/A')}
- Industry: {profile.get('industry', 'N/A')}
- Tech Stack: {profile.get('tech_stack', [])}
- Topics of Interest: {profile.get('topics_of_interest', [])}

RECENT TWEETS:
{json.dumps(tweets, indent=2)}

TIER CRITERIA:
- S: High influence (10K+ followers) + strong automation/AI signals
- A: 1K-10K followers + clear professional role + automation interest
- B: 500-1K followers + relevant industry (SaaS, tech)
- C: Followers of competitors OR mentions competitor tools
- D: Long-tail keyword matches only (low engagement potential)

Respond with JSON only:
{{"tier": "S|A|B|C|D", "confidence": 0.0-1.0, "reasoning": "brief explanation"}}"""

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
                "max_tokens": 300,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=30
        )
        
        if response.status_code != 200:
            return {"status": "error", "message": f"API error: {response.status_code}"}
        
        content = response.json()["content"][0]["text"].strip()
        
        # Remove markdown blocks
        if content.startswith("```"):
            match = re.search(r'```(?:json)?\s*(.*?)\s*```', content, re.DOTALL)
            if match:
                content = match.group(1).strip()
        
        classification = json.loads(content)
        return {
            "status": "success",
            "tier": classification.get("tier", "D"),
            "confidence": float(classification.get("confidence", 0.5)),
            "reasoning": classification.get("reasoning", ""),
            "profile_username": profile.get("username", "unknown")
        }
        
    except Exception as e:
        return {"status": "error", "message": str(e)}


if __name__ == "__main__":
    # Test with mock data
    test_profile = {
        "twitter_id": "123456",
        "username": "john_founder",
        "bio": "Founder @SaaSCo | Building automation tools | Ex-Zapier",
        "followers_count": 3400,
        "professional_role": "founder",
        "industry": "SaaS",
        "tech_stack": ["Python", "n8n", "Zapier"],
        "topics_of_interest": ["automation", "productivity", "AI tools"]
    }
    test_tweets = [
        "Just shipped our new workflow automation feature!",
        "AI agents are the future of productivity"
    ]
    print(wave_classifier(test_profile, test_tweets))
