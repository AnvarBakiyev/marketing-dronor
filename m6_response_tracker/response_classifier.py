"""M6 Response Classifier - Classify intent and sentiment of incoming responses."""

import json
from datetime import datetime
from typing import Optional


def response_classifier(
    response_text: str = "",
    conversation_history: str = "",
    anthropic_api_key_name: str = "anthropic_api_key",
    user_id: str = "69a54164-d42d-4590-a495-cfba67967a89",
    model: str = "claude-3-haiku-20240307"
) -> dict:
    """
    Classify intent and sentiment of incoming response.
    
    Args:
        response_text: Text of the response to classify
        conversation_history: JSON string of previous messages for context
        anthropic_api_key_name: Key name in KV store for Anthropic API key
        user_id: User ID for KV store access
        model: Claude model to use
        
    Returns:
        intent: positive/negative/question/neutral/conversion_signal
        sentiment: float -1.0 to 1.0
        urgency: low/medium/high
        confidence: float 0-1
        reasoning: explanation of classification
    """
    import requests
    
    if not response_text:
        return {"status": "error", "message": "response_text required"}
    
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
    
    # Parse conversation history if provided
    context_text = ""
    if conversation_history:
        try:
            history = json.loads(conversation_history)
            if isinstance(history, list):
                context_text = "\n\nPrevious conversation:\n"
                for msg in history[-5:]:  # Last 5 messages for context
                    direction = msg.get("direction", "unknown")
                    text = msg.get("text", "")
                    context_text += f"[{direction}]: {text}\n"
        except json.JSONDecodeError:
            context_text = f"\n\nContext: {conversation_history}"
    
    # Classification prompt
    classification_prompt = f"""Analyze this response in a business outreach conversation context.
{context_text}

Response to classify:
"{response_text}"

Classify this response and return a JSON object with:

1. "intent": One of:
   - "positive" - Interest, agreement, willingness to continue conversation
   - "negative" - Rejection, disinterest, request to stop
   - "question" - Asking for more information
   - "neutral" - Acknowledgment without clear direction
   - "conversion_signal" - Strong buying intent, asking about pricing, scheduling call, etc.

2. "sentiment": Float from -1.0 (very negative) to 1.0 (very positive)

3. "urgency": 
   - "high" - Time-sensitive, immediate response needed
   - "medium" - Should respond reasonably soon
   - "low" - Can respond at convenience

4. "confidence": Float from 0 to 1, how confident you are in this classification

5. "reasoning": Brief explanation (1-2 sentences)

6. "key_phrases": Array of important phrases that influenced classification

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
                "max_tokens": 500,
                "messages": [
                    {"role": "user", "content": classification_prompt}
                ]
            },
            timeout=30
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
            classification = json.loads(content)
        except json.JSONDecodeError:
            # Try to extract JSON from response
            import re
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                classification = json.loads(json_match.group())
            else:
                return {
                    "status": "error",
                    "message": "Failed to parse classification response",
                    "raw_response": content
                }
        
        # Validate and normalize response
        valid_intents = ["positive", "negative", "question", "neutral", "conversion_signal"]
        valid_urgencies = ["low", "medium", "high"]
        
        intent = classification.get("intent", "neutral").lower()
        if intent not in valid_intents:
            intent = "neutral"
        
        urgency = classification.get("urgency", "medium").lower()
        if urgency not in valid_urgencies:
            urgency = "medium"
        
        sentiment = float(classification.get("sentiment", 0))
        sentiment = max(-1.0, min(1.0, sentiment))  # Clamp to range
        
        confidence = float(classification.get("confidence", 0.5))
        confidence = max(0, min(1.0, confidence))  # Clamp to range
        
        return {
            "status": "success",
            "intent": intent,
            "sentiment": sentiment,
            "urgency": urgency,
            "confidence": confidence,
            "reasoning": classification.get("reasoning", ""),
            "key_phrases": classification.get("key_phrases", []),
            "response_text": response_text[:200],  # Truncate for reference
            "model_used": model,
            "timestamp": datetime.utcnow().isoformat()
        }
        
    except requests.exceptions.Timeout:
        return {"status": "error", "message": "Claude API timeout"}
    except Exception as e:
        return {"status": "error", "message": f"Classification failed: {str(e)}"}
