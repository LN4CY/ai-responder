# Copyright (c) 2026 ln4cy
# This software is released under the MIT License.
# See LICENSE file in the project root for full license details.

"""Google Gemini AI provider."""

import requests
import logging
import time
from .base import BaseProvider
import config
from config import load_system_prompt

logger = logging.getLogger(__name__)


class GeminiProvider(BaseProvider):
    """Google Gemini AI provider."""
    
    @property
    def name(self):
        return "Gemini"
    
    def _make_request(self, url, payload):
        """Make internal HTTP request to Gemini API."""
        return requests.post(url, json=payload, timeout=30)

    def get_response(self, prompt, history=None, context_id=None, location=None):
        """Get response from Gemini with grounding tools and optional fallback."""
        if not config.GEMINI_API_KEY:
            return "Error: Gemini API key missing."
        
        system_prompt = load_system_prompt('gemini', context_id=context_id)
        contents = []
        
        if history:
            for msg in history:
                role = 'model' if msg['role'] == 'assistant' else 'user'
                contents.append({'role': role, 'parts': [{'text': msg['content']}]})
        
        # Always append current prompt
        contents.append({'role': 'user', 'parts': [{'text': prompt}]})
        
        # Inject System Prompt
        if contents[0]['role'] == 'user':
            contents[0]['parts'][0]['text'] = f"{system_prompt}\n\n{contents[0]['parts'][0]['text']}"
        else:
            contents.insert(0, {'role': 'user', 'parts': [{'text': system_prompt}]})
        
        model = config.GEMINI_MODEL
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={config.GEMINI_API_KEY}"
        
        # 1. Prepare Grounding Payload
        tools = []
        if config.GEMINI_SEARCH_GROUNDING:
            tools.append({"google_search": {}})
        if config.GEMINI_MAPS_GROUNDING:
            tools.append({"google_maps": {}})
            
        payload = {"contents": contents}
        if tools:
            payload["tools"] = tools
            tool_config = {}
            if config.GEMINI_MAPS_GROUNDING and location and 'latitude' in location and 'longitude' in location:
                tool_config["retrieval_config"] = {
                    "lat_lng": {
                        "latitude": location['latitude'],
                        "longitude": location['longitude']
                    }
                }
            if tool_config:
                payload["tool_config"] = tool_config

        # 2. Execute Request with Retries and Fallback
        max_retries = 3
        retry_delay = 2
        
        for attempt in range(max_retries + 1):
            try:
                if attempt > 0:
                    logger.info(f"🔄 Retrying Gemini request (attempt {attempt}/{max_retries})...")
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                
                logger.info(f"Calling Gemini model: {model} (Grounding: {bool(tools)})")
                response = self._make_request(url, payload)
                
                # Check for transient errors to retry
                if response.status_code in [500, 502, 503, 504] and attempt < max_retries:
                    logger.warning(f"⚠️ Gemini service error ({response.status_code}). Retrying...")
                    continue

                # 3. Handle Unsupported Tool Fallback (Surgical Degraded Service)
                if response.status_code == 400 and tools:
                    error_data = response.json()
                    error_msg = error_data.get('error', {}).get('message', '').lower()
                    logger.warning(f"⚠️ Grounding issue detected: {error_msg}")

                    # Case A: Google Maps is specifically unsupported
                    if "google maps tool is not enabled" in error_msg or "google_maps" in error_msg:
                        logger.warning(f"📍 Model {model} rejects Google Maps. Retrying with Search only.")
                        new_tools = [t for t in tools if "google_maps" not in t]
                        if new_tools:
                            payload = payload.copy()
                            payload["tools"] = new_tools
                            payload.pop("tool_config", None) 
                            # Note: This is an internal retry/fallback, not counted against max_retries
                            response = self._make_request(url, payload)
                        else:
                            payload = payload.copy()
                            payload.pop("tools", None)
                            payload.pop("tool_config", None)
                            response = self._make_request(url, payload)

                    # Case B: General tool rejection or config rejection
                    elif any(kw in error_msg for kw in ["tool is not supported", "unknown name", "google_search_retrieval"]):
                        logger.warning(f"⚠️ Model {model} rejects grounding configuration. Falling back to standard chat.")
                        payload = payload.copy()
                        payload.pop("tools", None)
                        payload.pop("tool_config", None)
                        response = self._make_request(url, payload)

                # 4. Process Success or Terminal Error
                if response.status_code == 200:
                    data = response.json()
                    candidates = data.get('candidates', [])
                    if not candidates:
                        return "⚠️ No response generated (possibly filtered)"
                    
                    text = candidates[0]['content']['parts'][0]['text'].strip()
                    
                    # Check for grounding feedback
                    grounding = candidates[0].get('groundingMetadata', {})
                    if grounding and (grounding.get('webSearchQueries') or grounding.get('groundingChunks')):
                        logger.info(f"Gemini used search grounding")
                        text = f"🌐 {text}"
                    
                    return text
                else:
                    try:
                        error_info = response.json().get('error', {})
                        error_msg = error_info.get('message', 'Unknown error')
                        logger.error(f"Gemini API error ({response.status_code}): {error_msg}")
                        return self.format_error(response.status_code, error_msg)
                    except:
                        return f"❌ HTTP {response.status_code} error."

            except (requests.exceptions.RequestException, Exception) as e:
                if attempt < max_retries:
                    logger.warning(f"⚠️ Gemini request failed: {e}. Retrying...")
                    continue
                else:
                    logger.error(f"Gemini request final failure: {e}")
                    return f"❌ Unexpected error: {str(e)[:100]}"
        
        return "❌ Failed to get response after multiple attempts."
