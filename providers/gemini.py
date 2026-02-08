# Copyright (c) 2026 ln4cy
# This software is released under the MIT License.
# See LICENSE file in the project root for full license details.

"""Google Gemini AI provider."""

import requests
import logging
from .base import BaseProvider
from config import GEMINI_API_KEY, GEMINI_MODEL, GEMINI_SEARCH_GROUNDING, GEMINI_MAPS_GROUNDING, load_system_prompt

logger = logging.getLogger(__name__)


class GeminiProvider(BaseProvider):
    """Google Gemini AI provider."""
    
    @property
    def name(self):
        return "Gemini"
    
    def get_response(self, prompt, history=None, context_id=None):
        """Get response from Gemini."""
        if not GEMINI_API_KEY:
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
        
        # Use configured Gemini model
        model = GEMINI_MODEL
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
        
        # Grounding tools
        tools = []
        if GEMINI_SEARCH_GROUNDING:
            tools.append({"googleSearch": {}})
        if GEMINI_MAPS_GROUNDING:
            tools.append({"googleMaps": {}})
            
        payload = {"contents": contents}
        if tools:
            payload["tools"] = tools
            logger.info(f"Enabling Gemini grounding: {[list(t.keys())[0] for t in tools]}")
        
        try:
            logger.info(f"Calling Gemini model: {model}")
            response = requests.post(url, json=payload, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                candidates = data.get('candidates', [])
                if not candidates:
                    logger.error(f"Gemini returned 200 but no candidates: {data}")
                    # Check for safety filters or other blocks
                    if 'promptFeedback' in data:
                        feedback = data['promptFeedback']
                        if 'blockReason' in feedback:
                            return f"⚠️ Content blocked: {feedback['blockReason']}"
                    return "⚠️ No response generated (possibly filtered)"
                text = candidates[0]['content']['parts'][0]['text'].strip()
                return text
            else:
                # Parse error details
                try:
                    error_data = response.json()
                    error_info = error_data.get('error', {})
                    error_msg = error_info.get('message', 'Unknown error')
                    error_code = error_info.get('code', response.status_code)
                    
                    user_msg = self.format_error(response.status_code, error_msg)
                    logger.error(f"Gemini API error ({response.status_code}): {error_msg}")
                    return user_msg
                except:
                    logger.error(f"Gemini HTTP {response.status_code}: {response.text[:200]}")
                    return f"❌ HTTP {response.status_code} error. Check logs."
        except requests.exceptions.Timeout:
            logger.error("Gemini request timed out after 30s")
            return "⏱️ Request timed out (30s). Try a shorter message or try again."
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Gemini connection error: {e}")
            return "🌐 Connection failed. Check internet connection."
        except Exception as e:
            logger.error(f"Gemini request failed: {e}")
            return f"❌ Unexpected error: {str(e)[:100]}"
