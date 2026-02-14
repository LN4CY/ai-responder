# Copyright (c) 2026 ln4cy
# This software is released under the MIT License.
# See LICENSE file in the project root for full license details.

"""Ollama AI provider."""

import requests
import logging
import json
from .base import BaseProvider
from config import OLLAMA_HOST, OLLAMA_PORT, OLLAMA_MODEL, OLLAMA_MAX_MESSAGES, load_system_prompt

logger = logging.getLogger(__name__)


class OllamaProvider(BaseProvider):
    """Ollama local AI provider."""
    
    @property
    def name(self):
        return "Ollama"

    @property
    def supports_tools(self):
        """
        Ollama only supports tools in llama3.1+, mistral-nemo, etc.
        Check the model name for common support patterns.
        """
        model = self.config.get('ollama_model', OLLAMA_MODEL).lower()
        supported_patterns = ['3.1', 'nemo', 'vision', 'command-r', 'firefunction']
        return any(p in model for p in supported_patterns)

    def get_response(self, prompt, history=None, context_id=None, location=None, tools=None):
        """Get response from Ollama. Note: location is currently unused."""
        url = f"http://{OLLAMA_HOST}:{OLLAMA_PORT}/api/chat"
        
        system_prompt = load_system_prompt('ollama', context_id=context_id)
        messages = [{'role': 'system', 'content': system_prompt}]
        
        if history:
            # Limit context based on configuration
            messages.extend(history[-OLLAMA_MAX_MESSAGES:])
        else:
            messages.append({'role': 'user', 'content': prompt})
        
        # Prepare Ollama Tools (OpenAI Schema)
        ollama_tools = None
        if tools and self.supports_tools:
            ollama_tools = []
            for tool_key, tool_def in tools.items():
                ollama_tools.append({
                    "type": "function",
                    "function": {
                        "name": tool_def['declaration']['name'],
                        "description": tool_def['declaration']['description'],
                        "parameters": {
                            "type": "object",
                            "properties": tool_def['declaration']['parameters']['properties'],
                            "required": tool_def['declaration']['parameters'].get('required', [])
                        }
                    }
                })

        try:
            # Multi-turn tool loop
            for turn in range(5):
                payload = {
                    "model": OLLAMA_MODEL,
                    "messages": messages,
                    "stream": False
                }
                if ollama_tools:
                    payload["tools"] = ollama_tools

                response = requests.post(url, json=payload, timeout=300)
                if response.status_code != 200:
                    try:
                        error_data = response.json()
                        error_msg = error_data.get('error', 'Unknown error')
                        logger.error(f"Ollama error: {response.status_code} - {error_msg}")
                        return f"❌ Ollama error: {error_msg}"
                    except:
                        logger.error(f"Ollama HTTP {response.status_code}: {response.text[:200]}")
                        return f"❌ HTTP {response.status_code} error"

                data = response.json()
                message = data.get('message', {})
                content = message.get('content')
                tool_calls = message.get('tool_calls')

                # Add assistant message to tracking
                messages.append(message)

                if not tool_calls:
                    return content.strip() if content else "⚠️ No response from Ollama"

                # Execute tools
                logger.info(f"🛠️ Ollama requested {len(tool_calls)} tool calls.")
                for tool_call in tool_calls:
                    function_name = tool_call['function']['name']
                    arguments = tool_call['function']['arguments']
                    
                    if function_name in tools:
                        handler = tools[function_name]['handler']
                        try:
                            result = handler(**arguments)
                            logger.info(f"✅ Tool {function_name} result: {str(result)[:100]}")
                            messages.append({
                                "role": "tool",
                                "content": json.dumps(result)
                            })
                        except Exception as e:
                            logger.error(f"❌ Error executing tool {function_name}: {e}")
                            messages.append({
                                "role": "tool",
                                "content": json.dumps({"error": str(e)})
                            })
                    else:
                        logger.warning(f"⚠️ Tool {function_name} not found.")
                        messages.append({
                            "role": "tool",
                            "content": "Tool not found"
                        })
            
            return "⚠️ Ollama tool loop exceeded max turns."
        except requests.exceptions.Timeout:
            logger.error("Ollama request timed out after 300s")
            return "⏱️ Request timed out (5min). Model may be too slow or overloaded."
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Ollama connection error: {e}")
            return f"🌐 Cannot connect to Ollama at {OLLAMA_HOST}:{OLLAMA_PORT}. Is it running?"
        except Exception as e:
            logger.error(f"Ollama error: {e}")
            return f"❌ Unexpected error: {str(e)[:100]}"
