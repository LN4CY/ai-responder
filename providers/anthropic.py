# Copyright (c) 2026 ln4cy
# This software is released under the MIT License.
# See LICENSE file in the project root for full license details.

"""Anthropic Claude AI provider."""

import requests
import logging
import json
from .base import BaseProvider
from config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL, load_system_prompt

logger = logging.getLogger(__name__)


class AnthropicProvider(BaseProvider):
    """Anthropic Claude AI provider."""
    
    @property
    def name(self):
        return "Anthropic"
    
    @property
    def supports_tools(self):
        """Claude 3 family supports function calling."""
        return True

    def get_response(self, prompt, history=None, context_id=None, location=None, tools=None):
        """Get response from Anthropic. Note: location is currently unused."""
        if not ANTHROPIC_API_KEY:
            return "Error: Anthropic API key missing."
        
        url = 'https://api.anthropic.com/v1/messages'
        
        system_prompt = load_system_prompt('anthropic', context_id=context_id)
        messages = []
        
        if history:
            messages.extend(history)
        else:
            messages.append({'role': 'user', 'content': prompt})
        
        # Prepare Anthropic Tools
        anthropic_tools = None
        if tools:
            anthropic_tools = []
            for tool_key, tool_def in tools.items():
                anthropic_tools.append({
                    "name": tool_def['declaration']['name'],
                    "description": tool_def['declaration']['description'],
                    "input_schema": {
                        "type": "object",
                        "properties": tool_def['declaration']['parameters']['properties'],
                        "required": tool_def['declaration']['parameters'].get('required', [])
                    }
                })

        headers = {
            'Content-Type': 'application/json',
            'x-api-key': ANTHROPIC_API_KEY,
            'anthropic-version': '2023-06-01'
        }

        try:
            # Multi-turn tool loop
            for turn in range(5):
                payload = {
                    'model': ANTHROPIC_MODEL,
                    'max_tokens': 150,
                    'system': system_prompt,
                    'messages': messages
                }
                if anthropic_tools:
                    payload['tools'] = anthropic_tools

                response = requests.post(url, json=payload, headers=headers, timeout=30)
                if response.status_code != 200:
                    try:
                        error_data = response.json()
                        error_msg = error_data.get('error', {}).get('message', 'Unknown error')
                        user_msg = self.format_error(response.status_code, error_msg)
                        logger.error(f"Anthropic error: {response.status_code} - {error_msg}")
                        return user_msg
                    except:
                        logger.error(f"Anthropic HTTP {response.status_code}: {response.text[:200]}")
                        return f"❌ HTTP {response.status_code} error"

                data = response.json()
                content_blocks = data.get('content', [])
                
                # Add assistant message to tracking
                messages.append({'role': 'assistant', 'content': content_blocks})

                tool_use_blocks = [b for b in content_blocks if b['type'] == 'tool_use']
                text_blocks = [b for b in content_blocks if b['type'] == 'text']
                final_text = "".join([b['text'] for b in text_blocks]).strip()

                if not tool_use_blocks:
                    return final_text if final_text else "⚠️ No response from Anthropic"

                # Execute tools
                logger.info(f"🛠️ Anthropic requested {len(tool_use_blocks)} tool calls.")
                tool_results = []
                for tool_block in tool_use_blocks:
                    function_name = tool_block['name']
                    arguments = tool_block['input']
                    tool_use_id = tool_block['id']
                    
                    if function_name in tools:
                        handler = tools[function_name]['handler']
                        try:
                            result = handler(**arguments)
                            logger.info(f"✅ Tool {function_name} result: {str(result)[:100]}")
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": tool_use_id,
                                "content": json.dumps(result)
                            })
                        except Exception as e:
                            logger.error(f"❌ Error executing tool {function_name}: {e}")
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": tool_use_id,
                                "content": json.dumps({"error": str(e)}),
                                "is_error": True
                            })
                    else:
                        logger.warning(f"⚠️ Tool {function_name} not found in available tools.")
                        tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": tool_use_id,
                                "content": "Tool not found",
                                "is_error": True
                            })
                
                # Anthropic expects an array of tool_results in a single "user" message
                messages.append({'role': 'user', 'content': tool_results})
            
            return "⚠️ Anthropic tool loop exceeded max turns."
        except requests.exceptions.Timeout:
            logger.error("Anthropic request timed out")
            return "⏱️ Request timed out. Try again."
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Anthropic connection error: {e}")
            return "🌐 Connection failed. Check internet."
        except Exception as e:
            logger.error(f"Anthropic error: {e}")
            return f"❌ Unexpected error: {str(e)[:100]}"
