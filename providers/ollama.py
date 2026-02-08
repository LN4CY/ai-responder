# Copyright (c) 2026 ln4cy
# This software is released under the MIT License.
# See LICENSE file in the project root for full license details.

"""Ollama AI provider."""

import requests
import logging
from .base import BaseProvider
from config import OLLAMA_HOST, OLLAMA_PORT, OLLAMA_MODEL, OLLAMA_MAX_MESSAGES, load_system_prompt

logger = logging.getLogger(__name__)


class OllamaProvider(BaseProvider):
    """Ollama local AI provider."""
    
    @property
    def name(self):
        return "Ollama"
    
    def get_response(self, prompt, history=None, context_id=None, location=None):
        """Get response from Ollama."""
        url = f"http://{OLLAMA_HOST}:{OLLAMA_PORT}/api/chat"
        
        system_prompt = load_system_prompt('ollama', context_id=context_id)
        messages = [{'role': 'system', 'content': system_prompt}]
        
        if history:
            # Limit context based on configuration
            messages.extend(history[-OLLAMA_MAX_MESSAGES:])
        else:
            messages.append({'role': 'user', 'content': prompt})
        
        payload = {
            "model": OLLAMA_MODEL,
            "messages": messages,
            "stream": False
        }
        
        try:
            response = requests.post(url, json=payload, timeout=300)
            
            if response.status_code == 200:
                data = response.json()
                content = data.get('message', {}).get('content', '')
                if content:
                    return content
                else:
                    logger.error(f"Ollama returned 200 but no content: {data}")
                    return "⚠️ No response from Ollama"
            else:
                try:
                    error_data = response.json()
                    error_msg = error_data.get('error', 'Unknown error')
                    
                    if response.status_code == 404:
                        user_msg = f"❌ Model '{OLLAMA_MODEL}' not found. Pull it first: ollama pull {OLLAMA_MODEL}"
                    elif response.status_code == 500:
                        user_msg = "🔧 Ollama server error. Check if model is loaded."
                    elif response.status_code == 503:
                        user_msg = "⏱️ Ollama service unavailable. Is it running?"
                    else:
                        user_msg = self.format_error(response.status_code, error_msg)
                    
                    logger.error(f"Ollama error: {response.status_code} - {error_msg}")
                    return user_msg
                except:
                    logger.error(f"Ollama HTTP {response.status_code}: {response.text[:200]}")
                    return f"❌ HTTP {response.status_code} error"
        except requests.exceptions.Timeout:
            logger.error("Ollama request timed out after 300s")
            return "⏱️ Request timed out (5min). Model may be too slow or overloaded."
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Ollama connection error: {e}")
            return f"🌐 Cannot connect to Ollama at {OLLAMA_HOST}:{OLLAMA_PORT}. Is it running?"
        except Exception as e:
            logger.error(f"Ollama error: {e}")
            return f"❌ Unexpected error: {str(e)[:100]}"
