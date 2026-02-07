"""Anthropic Claude AI provider."""

import requests
import logging
from .base import BaseProvider
from config import ANTHROPIC_API_KEY, load_system_prompt

logger = logging.getLogger(__name__)


class AnthropicProvider(BaseProvider):
    """Anthropic Claude AI provider."""
    
    @property
    def name(self):
        return "Anthropic"
    
    def get_response(self, prompt, history=None):
        """Get response from Anthropic."""
        if not ANTHROPIC_API_KEY:
            return "Error: Anthropic API key missing."
        
        url = 'https://api.anthropic.com/v1/messages'
        
        system_prompt = load_system_prompt('anthropic')
        messages = []
        
        if history:
            messages.extend(history)
        else:
            messages.append({'role': 'user', 'content': prompt})
        
        payload = {
            'model': 'claude-3-haiku-20240307',
            'max_tokens': 150,
            'system': system_prompt,
            'messages': messages
        }
        headers = {
            'Content-Type': 'application/json',
            'x-api-key': ANTHROPIC_API_KEY,
            'anthropic-version': '2023-06-01'
        }
        
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            if response.status_code == 200:
                data = response.json()
                return data.get('content', [{}])[0].get('text', '').strip()
            else:
                try:
                    error_data = response.json()
                    error_msg = error_data.get('error', {}).get('message', 'Unknown error')
                    user_msg = self.format_error(response.status_code, error_msg)
                    logger.error(f"Anthropic error: {response.status_code} - {error_msg}")
                    return user_msg
                except:
                    logger.error(f"Anthropic HTTP {response.status_code}: {response.text[:200]}")
                    return f"❌ HTTP {response.status_code} error"
        except requests.exceptions.Timeout:
            logger.error("Anthropic request timed out")
            return "⏱️ Request timed out. Try again."
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Anthropic connection error: {e}")
            return "🌐 Connection failed. Check internet."
        except Exception as e:
            logger.error(f"Anthropic error: {e}")
            return f"❌ Unexpected error: {str(e)[:100]}"
