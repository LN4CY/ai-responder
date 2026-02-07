"""OpenAI AI provider."""

import requests
import logging
from .base import BaseProvider
from config import OPENAI_API_KEY, load_system_prompt

logger = logging.getLogger(__name__)


class OpenAIProvider(BaseProvider):
    """OpenAI GPT provider."""
    
    @property
    def name(self):
        return "OpenAI"
    
    def get_response(self, prompt, history=None, context_id=None):
        """Get response from OpenAI."""
        if not OPENAI_API_KEY:
            return "Error: OpenAI API key missing."
        
        url = 'https://api.openai.com/v1/chat/completions'
        
        system_prompt = load_system_prompt('openai', context_id=context_id)
        messages = [{'role': 'system', 'content': system_prompt}]
        
        if history:
            messages.extend(history)
        else:
            messages.append({'role': 'user', 'content': prompt})
        
        payload = {
            'model': 'gpt-3.5-turbo',
            'messages': messages,
            'max_tokens': 150
        }
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {OPENAI_API_KEY}'
        }
        
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            if response.status_code == 200:
                data = response.json()
                return data.get('choices', [{}])[0].get('message', {}).get('content', '').strip()
            else:
                try:
                    error_data = response.json()
                    error_msg = error_data.get('error', {}).get('message', 'Unknown error')
                    user_msg = self.format_error(response.status_code, error_msg)
                    logger.error(f"OpenAI error: {response.status_code} - {error_msg}")
                    return user_msg
                except:
                    logger.error(f"OpenAI HTTP {response.status_code}: {response.text[:200]}")
                    return f"❌ HTTP {response.status_code} error"
        except requests.exceptions.Timeout:
            logger.error("OpenAI request timed out")
            return "⏱️ Request timed out. Try again."
        except requests.exceptions.ConnectionError as e:
            logger.error(f"OpenAI connection error: {e}")
            return "🌐 Connection failed. Check internet."
        except Exception as e:
            logger.error(f"OpenAI error: {e}")
            return f"❌ Unexpected error: {str(e)[:100]}"
