"""
Wave Classifier Expert
Classify Twitter profile into Tier S/A/B/C/D for wave outreach strategy.

MKT-10: https://linear.app/extella/issue/MKT-10
"""
import requests
import json
import re


def wave_classifier(
    profile: dict,
    tweets: list,
    anthropic_api_key: str
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
        profile: dict with profile data
        tweets: list of recent tweets
        anthropic_api_key: Claude API key
    
    Returns:
        dict with tier, confidence, reasoning
    """
    if not anthropic_api_key:
        return {"status": "error", "message": "anthropic_api_key is required"}
    
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
- S: High influence (10K+ followers) + strong automation/AI signals in bio or tweets
- A: 1K-10K followers + clear professional role (founder, engineer, etc.) + automation interest
- B: 500-1K followers + relevant industry (SaaS, tech, etc.)
- C: Followers of competitors OR mentions competitor tools
- D: Long-tail keyword matches only (low engagement potential)

Respond ONLY with valid JSON (no markdown, no code blocks):
{{"tier": "S|A|B|C|D", "confidence": 0.0-1.0, "reasoning": "brief explanation"}}"""

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
                "max_tokens": 300,
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
            return {
                "status": "success",
                "tier": classification.get("tier", "D"),
                "confidence": float(classification.get("confidence", 0.5)),
                "reasoning": classification.get("reasoning", "No reasoning provided"),
                "profile_username": profile.get("username", "unknown")
            }
        except json.JSONDecodeError:
            # Fallback: try to extract tier from text
            tier_match = re.search(r'"tier":\s*"([SABCD])"', content)
            conf_match = re.search(r'"confidence":\s*([\d.]+)', content)
            
            return {
                "status": "partial",
                "tier": tier_match.group(1) if tier_match else "D",
                "confidence": float(conf_match.group(1)) if conf_match else 0.3,
                "reasoning": "Extracted from malformed response",
                "raw_response": content[:500]
            }
            
    except requests.exceptions.Timeout:
        return {"status": "error", "message": "Claude API timeout"}
    except requests.exceptions.RequestException as e:
        return {"status": "error", "message": f"Request failed: {str(e)}"}
