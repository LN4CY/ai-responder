# Copyright (c) 2026 ln4cy
# This software is released under the MIT License.
# See LICENSE file in the project root for full license details.

"""Anthropic Claude AI provider."""

import requests
import logging
import json
from .base import BaseProvider
from config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL, ANTHROPIC_THINKING_MODEL, load_system_prompt
from .routing import classify_complexity

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

    def get_response(self, prompt, history=None, context_id=None, location=None, tools=None, mcp_client=None):
        """Get response from Anthropic."""
        if not ANTHROPIC_API_KEY:
            return "Error: Anthropic API key missing."
        
        complexity = classify_complexity(prompt, history)
        thinking_model = self.config.get('anthropic_thinking_model', ANTHROPIC_THINKING_MODEL)
        model = thinking_model if complexity == 'complex' else ANTHROPIC_MODEL
        logger.info(f"[routing] complexity={complexity} → {model}")

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
            for mcp_tool in tools:
                # Sanitize: Anthropic/Claude is strict about JSON Schema fields
                params = mcp_tool.get('inputSchema', {"type": "object", "properties": {}}).copy()
                params.pop('$schema', None)
                
                anthropic_tools.append({
                    "name": mcp_tool['name'],
                    "description": mcp_tool.get('description', ''),
                    "input_schema": params
                })

        headers = {
            'Content-Type': 'application/json',
            'x-api-key': ANTHROPIC_API_KEY,
            'anthropic-version': '2023-06-01'
        }

        try:
            # Multi-turn tool loop
            action_tools_executed = 0
            silent_ack_tools = 0
            
            for turn in range(5):
                payload = {
                    'model': model,
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
                    except Exception:
                        logger.error(f"Anthropic HTTP {response.status_code}: {response.text[:200]}")
                        return f"❌ HTTP {response.status_code} error"

                data = response.json()
                content_blocks = data.get('content', [])
                
                # Add assistant message to tracking
                messages.append({'role': 'assistant', 'content': content_blocks})

                tool_use_blocks = [b for b in content_blocks if b.get('type') == 'tool_use']
                text_blocks = [b for b in content_blocks if b.get('type') == 'text']
                final_text = "".join([b.get('text', '') for b in text_blocks]).strip()

                if not tool_use_blocks:
                    # If ALL action tools in this session were handled proactively, the AI
                    # should stay silent. We ignore info tools (like get_node_details)
                    # since they don't justify a conversational follow-up on their own.
                    if action_tools_executed > 0 and silent_ack_tools == action_tools_executed:
                        logger.info("🔇 All Anthropic action tools handled proactively. Suppressing final text.")
                        return "__SILENT_ACK__"
                    return final_text if final_text else "⚠️ No response from Anthropic"

                # Execute tools
                logger.info(f"🛠️ Anthropic requested {len(tool_use_blocks)} tool calls.")
                tool_results = []
                for tool_block in tool_use_blocks:
                    function_name = tool_block['name']
                    arguments = tool_block['input']
                    tool_use_id = tool_block['id']
                    
                    if mcp_client:
                        try:
                            raw_result = mcp_client.call_tool(function_name, arguments)
                            if hasattr(raw_result, 'content') and isinstance(raw_result.content, list):
                                result = "\n".join([getattr(c, 'text', str(c)) for c in raw_result.content])
                            else:
                                result = raw_result
                                
                            logger.info(f"✅ Tool {function_name} result: {str(result)[:100]}")
                            
                            # (Action tools / Silent ACK logic removed for MCP flexibility)
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
                        logger.warning(f"⚠️ MCP Client missing. Tool {function_name} cannot be executed.")
                        tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": tool_use_id,
                                "content": "MCP Client unavailable",
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
