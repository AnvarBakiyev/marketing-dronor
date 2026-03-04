"""M6 Response Generator - Generate suggested reply variants for operator."""

import json
from datetime import datetime
from typing import List, Optional


def response_generator(
    response_text: str = "",
    intent: str = "neutral",
    sentiment: float = 0.0,
    conversation_history: str = "",
    target_profile: str = "",
    product_context: str = "",
    anthropic_api_key_name: str = "anthropic_api_key",
    user_id: str = "69a54164-d42d-4590-a495-cfba67967a89",
    model: str = "claude-3-5-sonnet-20241022",
    num_variants: int = 3
) -> dict:
    """
    Generate suggested reply variants for operator based on incoming response.
    
    Args:
        response_text: The incoming message to respond to
        intent: Classified intent (positive/negative/question/neutral/conversion_signal)
        sentiment: Sentiment score from -1.0 to 1.0
        conversation_history: JSON string of previous messages
        target_profile: JSON string with target's profile info (name, bio, interests)
        product_context: Context about what we're offering
        anthropic_api_key_name: Key name in KV store for Anthropic API key
        user_id: User ID for KV store access
        model: Claude model to use (sonnet for better quality)
        num_variants: Number of reply variants to generate (1-5)
        
    Returns:
        variants: List of suggested replies with tone labels
        recommended: Index of recommended variant based on context
        reasoning: Why the recommended variant is suggested
    """
    import requests
    
    if not response_text:
        return {"status": "error", "message": "response_text required"}
    
    num_variants = max(1, min(5, num_variants))  # Clamp to 1-5
    
    # Get Anthropic API key from KV store
    try:
        kv_response = requests.post(
            "http://3.211.238.155:9100/api/kv/get",
            json={"key": anthropic_api_key_name, "user_id": user_id},
            timeout=10
        )
        if kv_response.status_code != 200:
            return {"status": "error", "message": f"Failed to get API key: {kv_response.text}"}
        api_key = kv_response.json().get("value")
        if not api_key:
            return {"status": "error", "message": "Anthropic API key not found in KV store"}
    except Exception as e:
        return {"status": "error", "message": f"KV store error: {str(e)}"}
    
    # Parse conversation history
    history_text = ""
    if conversation_history:
        try:
            history = json.loads(conversation_history)
            if isinstance(history, list):
                history_text = "\n\nConversation history:\n"
                for msg in history[-7:]:  # Last 7 messages
                    direction = msg.get("direction", "unknown")
                    text = msg.get("text", "")
                    ts = msg.get("timestamp", "")
                    history_text += f"[{direction}] {text}\n"
        except json.JSONDecodeError:
            pass
    
    # Parse target profile
    profile_text = ""
    if target_profile:
        try:
            profile = json.loads(target_profile)
            profile_text = f"\n\nTarget profile:\n"
            if profile.get("name"):
                profile_text += f"Name: {profile['name']}\n"
            if profile.get("bio"):
                profile_text += f"Bio: {profile['bio']}\n"
            if profile.get("interests"):
                profile_text += f"Interests: {', '.join(profile['interests'])}\n"
            if profile.get("company"):
                profile_text += f"Company: {profile['company']}\n"
            if profile.get("role"):
                profile_text += f"Role: {profile['role']}\n"
        except json.JSONDecodeError:
            profile_text = f"\n\nTarget context: {target_profile}"
    
    # Product context
    product_text = ""
    if product_context:
        product_text = f"\n\nProduct/service context: {product_context}"
    
    # Build strategy based on intent
    intent_strategies = {
        "positive": "Build on their interest. Move conversation forward. Suggest next step.",
        "negative": "Acknowledge gracefully. Keep door open. Don't push. One variant can try light reframe.",
        "question": "Answer their question directly. Provide value. Ask engaging follow-up.",
        "neutral": "Add value to re-engage. Ask question to understand their needs better.",
        "conversion_signal": "Strike while hot! Provide clear next step (call, demo, pricing). Make it easy to say yes."
    }
    
    strategy = intent_strategies.get(intent, intent_strategies["neutral"])
    
    # Generation prompt
    generation_prompt = f"""You are an expert sales conversation assistant. Generate {num_variants} reply variants for a business outreach conversation.
{history_text}
{profile_text}
{product_text}

---
Their latest message:
"{response_text}"

Classified as:
- Intent: {intent}
- Sentiment: {sentiment} (-1 to 1 scale)

Strategy for this intent: {strategy}

---

Generate {num_variants} reply variants. For each variant:
1. Different tone/approach (professional, casual, direct, consultative, etc.)
2. Keep it concise (2-4 sentences max for Twitter/DM context)
3. Natural, human language (not salesy or robotic)
4. Personalized if profile info available

Return a JSON object with:
{{
  "variants": [
    {{
      "text": "The reply text",
      "tone": "professional/casual/direct/consultative/friendly",
      "approach": "Brief description of the approach"
    }}
  ],
  "recommended": 0,  // Index of recommended variant (0-based)
  "reasoning": "Why this variant is recommended for this specific situation"
}}

Respond ONLY with valid JSON, no other text."""

    # Call Claude API
    try:
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": model,
                "max_tokens": 1500,
                "messages": [
                    {"role": "user", "content": generation_prompt}
                ]
            },
            timeout=60
        )
        
        if response.status_code != 200:
            return {
                "status": "error",
                "message": f"Claude API error: {response.status_code} - {response.text}"
            }
        
        result = response.json()
        content = result.get("content", [{}])[0].get("text", "")
        
        # Parse Claude's JSON response
        try:
            generated = json.loads(content)
        except json.JSONDecodeError:
            # Try to extract JSON from response
            import re
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                generated = json.loads(json_match.group())
            else:
                return {
                    "status": "error",
                    "message": "Failed to parse generation response",
                    "raw_response": content
                }
        
        variants = generated.get("variants", [])
        recommended = generated.get("recommended", 0)
        reasoning = generated.get("reasoning", "")
        
        # Validate recommended index
        if not isinstance(recommended, int) or recommended < 0 or recommended >= len(variants):
            recommended = 0
        
        return {
            "status": "success",
            "variants": variants,
            "recommended": recommended,
            "reasoning": reasoning,
            "input_intent": intent,
            "input_sentiment": sentiment,
            "num_variants": len(variants),
            "model_used": model,
            "timestamp": datetime.utcnow().isoformat()
        }
        
    except requests.exceptions.Timeout:
        return {"status": "error", "message": "Claude API timeout"}
    except Exception as e:
        return {"status": "error", "message": f"Generation failed: {str(e)}"}
