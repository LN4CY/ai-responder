# Copyright (c) 2026 ln4cy
# This software is released under the MIT License.
# See LICENSE file in the project root for full license details.

"""OpenAI AI provider."""

import requests
import logging
import json
from .base import BaseProvider
from config import OPENAI_API_KEY, OPENAI_MODEL, load_system_prompt

logger = logging.getLogger(__name__)


class OpenAIProvider(BaseProvider):
    """OpenAI GPT provider."""
    
    @property
    def name(self):
        return "OpenAI"
    
    @property
    def supports_tools(self):
        """Modern GPT models support function calling."""
        return True

    def get_response(self, prompt, history=None, context_id=None, location=None, tools=None, mcp_client=None):
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
        
        # Prepare MCP Tools for OpenAI
        openai_tools = None
        if tools:
            openai_tools = []
            for mcp_tool in tools:
                # Sanitize: OpenAI rejects meta-schema fields like '$schema'
                params = mcp_tool.get('inputSchema', {"type": "object", "properties": {}}).copy()
                params.pop('$schema', None)
                
                openai_tools.append({
                    "type": "function",
                    "function": {
                        "name": mcp_tool['name'],
                        "description": mcp_tool.get('description', ''),
                        "parameters": params
                    }
                })

        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {OPENAI_API_KEY}'
        }

        try:
            # Multi-turn tool loop
            action_tools_executed = 0
            silent_ack_tools = 0
            # Tools that represent a side effect or request (vs simple lookups)
            action_tools = {"request_node_telemetry", "watch_condition", "watch_node_online", 
                            "rm_proactive_task", "send_message"}
            
            for turn in range(5):
                payload = {
                    'model': OPENAI_MODEL,
                    'messages': messages,
                    'max_tokens': 150
                }
                if openai_tools:
                    payload['tools'] = openai_tools
                    payload['tool_choice'] = 'auto'

                response = requests.post(url, json=payload, headers=headers, timeout=30)
                if response.status_code != 200:
                    try:
                        error_data = response.json()
                        error_msg = error_data.get('error', {}).get('message', 'Unknown error')
                        user_msg = self.format_error(response.status_code, error_msg)
                        logger.error(f"OpenAI error: {response.status_code} - {error_msg}")
                        return user_msg
                    except:
                        logger.error(f"OpenAI HTTP {response.status_code}: {response.text[:200]}")
                        return f"❌ HTTP {response.status_code} error"

                data = response.json()
                message = data.get('choices', [{}])[0].get('message', {})
                content = message.get('content')
                tool_calls = message.get('tool_calls')

                # Add assistant message to tracking
                messages.append(message)

                if not tool_calls:
                    # If ALL action tools in this session were handled proactively, the AI
                    # should stay silent. We ignore info tools (like get_node_details)
                    # since they don't justify a conversational follow-up on their own.
                    if action_tools_executed > 0 and silent_ack_tools == action_tools_executed:
                        logger.info("🔇 All OpenAI action tools handled proactively. Suppressing final text.")
                        return "__SILENT_ACK__"
                    return content.strip() if content else "⚠️ No response from OpenAI"

                # Execute tools
                logger.info(f"🛠️ OpenAI requested {len(tool_calls)} tool calls.")
                for tool_call in tool_calls:
                    function_name = tool_call['function']['name']
                    arguments = json.loads(tool_call['function']['arguments'])
                    
                    if mcp_client:
                        try:
                            result = mcp_client.call_tool(function_name, arguments)
                            logger.info(f"✅ Tool {function_name} result: {str(result)[:100]}")
                            
                            messages.append({
                                "tool_call_id": tool_call['id'],
                                "role": "tool",
                                "name": function_name,
                                "content": json.dumps(result)
                            })
                        except Exception as e:
                            logger.error(f"❌ Error executing tool {function_name}: {e}")
                            messages.append({
                                "tool_call_id": tool_call['id'],
                                "role": "tool",
                                "name": function_name,
                                "content": json.dumps({"error": str(e)})
                            })
                    else:
                        logger.warning(f"⚠️ MCP Client missing. Tool {function_name} cannot be executed.")
                        messages.append({
                            "tool_call_id": tool_call['id'],
                            "role": "tool",
                            "name": function_name,
                            "content": json.dumps({"error": "MCP Client unavailable"})
                        })
            
            return "⚠️ OpenAI tool loop exceeded max turns."
        except requests.exceptions.Timeout:
            logger.error("OpenAI request timed out")
            return "⏱️ Request timed out. Try again."
        except requests.exceptions.ConnectionError as e:
            logger.error(f"OpenAI connection error: {e}")
            return "🌐 Connection failed. Check internet."
        except Exception as e:
            logger.error(f"OpenAI error: {e}")
            return f"❌ Unexpected error: {str(e)[:100]}"
