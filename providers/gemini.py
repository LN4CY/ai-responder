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

import threading as _threading

logger = logging.getLogger(__name__)


class GeminiProvider(BaseProvider):
    """Google Gemini AI provider."""
    
    @property
    def name(self):
        return "Gemini"
    
    def _make_request(self, url, payload):
        """Make internal HTTP request to Gemini API.
        
        Runs inside a daemon thread with a hard wall-clock timeout to guard
        against Python's getaddrinfo() DNS hang, which bypasses socket and
        requests timeouts on Linux when Docker's internal DNS is unreachable.
        """
        result = [None]
        exc = [None]

        def _do_request():
            session = requests.Session()
            session.headers.update({'Connection': 'close'})
            try:
                result[0] = session.post(url, json=payload, timeout=(10, 30))
            except Exception as e:
                exc[0] = e
            finally:
                session.close()

        t = _threading.Thread(target=_do_request, daemon=True)
        t.start()
        t.join(timeout=45)  # Hard wall-clock limit (survives getaddrinfo hang)

        if t.is_alive():
            raise requests.exceptions.Timeout(
                "Gemini request timed out after 45s (possible DNS hang)"
            )
        if exc[0] is not None:
            raise exc[0]
        return result[0]


    @property
    def supports_tools(self):
        """Gemini Flash/Pro models support function calling."""
        return True

    def get_response(self, prompt, history=None, context_id=None, location=None, tools=None):
        """Get response from Gemini with grounding tools, custom tools, and optional fallback."""
        api_key = self.config.get('gemini_api_key', config.GEMINI_API_KEY)
        if not api_key:
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
        
        model = self.config.get('gemini_model', config.GEMINI_MODEL)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        
        # 1. Prepare Tools Payload
        gemini_tools = []
        
        # Check if we have custom tools to execute
        has_custom_tools = False
        if tools:
            has_custom_tools = True
            
        # Grounding tools (Search/Maps) are often mutually exclusive with Function Calling 
        # in strict models or specific API versions. Prioritize Function Calling.
        if not has_custom_tools:
            if self.config.get('gemini_search_grounding', config.GEMINI_SEARCH_GROUNDING):
                gemini_tools.append({"google_search": {}})
            if self.config.get('gemini_maps_grounding', config.GEMINI_MAPS_GROUNDING):
                gemini_tools.append({"google_maps": {}})
        else:
             logger.info("🔧 Custom tools active - disabling incompatible Google Search grounding.")
            
        # Add custom Meshtastic tools if provided
        custom_tool_map = {}
        if tools:
            function_declarations = []
            for t_name, t_info in tools.items():
                function_declarations.append(t_info['declaration'])
                custom_tool_map[t_name] = t_info['handler']
            
            # Dynamic Grounding: Inject a "stub" search tool to let the AI request search.
            if has_custom_tools and self.config.get('gemini_search_grounding', config.GEMINI_SEARCH_GROUNDING):
                function_declarations.append({
                     "name": "google_search_stub",
                     "description": "Search the web for real-time information, news, or general knowledge using Google.",
                     "parameters": {"type": "OBJECT", "properties": {"query": {"type": "STRING", "description": "The search query."}}, "required": ["query"]}
                })

            if function_declarations:
                gemini_tools.append({"function_declarations": function_declarations})

        payload = {"contents": contents}
        if gemini_tools:
            payload["tools"] = gemini_tools

        # 2. Execute Request with Retries and Fallback
        max_retries = 3
        retry_delay = 2
        
        # State: Force native grounding for this turn?
        force_grounding_turn = False
        
        for attempt in range(max_retries + 1):
            try:
                # Dynamic Switching: If forced, REPLACE tools with only Google Search/Maps
                current_tools = gemini_tools
                if force_grounding_turn:
                    logger.info("🌍 Dynamic Switch: Promoting to Native Google Grounding for this turn.")
                    current_tools = [{"google_search": {}}]
                    if self.config.get('gemini_maps_grounding', config.GEMINI_MAPS_GROUNDING):
                        current_tools.append({"google_maps": {}})
                    payload["tools"] = current_tools
                
                if attempt > 0 and not force_grounding_turn:
                    logger.info(f"🔄 Retrying Gemini request (attempt {attempt}/{max_retries})...")
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                
                logger.info(f"Calling Gemini model: {model} (Tools: {len(current_tools)})")
                
                # Turn loop for function calling
                max_turns = 5
                for turn in range(max_turns):
                    response = self._make_request(url, payload)
                    
                    # Check for transient errors to retry (outer loop)
                    if response.status_code in [500, 502, 503, 504] and attempt < max_retries:
                        logger.warning(f"⚠️ Gemini service error ({response.status_code}). Retrying...")
                        break # break turn loop, fall back to attempt retry
                    
                    # Process Success
                    if response.status_code == 200:
                        data = response.json()
                        candidates = data.get('candidates', [])
                        if not candidates:
                            return "⚠️ No response generated"
                        
                        part = candidates[0]['content']['parts'][0]
                        
                        # A. Handle Function Call
                        if "functionCall" in part:
                            f_call = part["functionCall"]
                            f_name = f_call["name"]
                            f_args = f_call.get("args", {})
                            
                            # DYNAMIC SWITCH INTERCEPTION
                            if f_name == "google_search_stub":
                                logger.info(f"🕵️ Intercepted Search Stub call: query='{f_args.get('query')}'")
                                force_grounding_turn = True
                                # Add the AI's call to history? No, we retry the whole turn.
                                # Breaking inner loop triggers outer loop retry?
                                # We need to ensure logic below handles this.
                                break 

                            logger.info(f"🤖 AI requested tool: {f_name}({f_args})")
                            
                            if f_name in custom_tool_map:
                                try:
                                    result = custom_tool_map[f_name](**f_args)
                                    logger.info(f"✅ Tool result: {str(result)[:100]}...")
                                except Exception as e:
                                    logger.error(f"❌ Error executing tool {f_name}: {e}")
                                    result = f"Error: {str(e)}"
                            else:
                                logger.warning(f"⚠️ AI requested unknown tool: {f_name}")
                                result = "Error: Tool not found"
                            
                            # Add function call and response to contents
                            payload["contents"].append({
                                "role": "model",
                                "parts": [part]
                            })
                            payload["contents"].append({
                                "role": "function",
                                "parts": [{
                                    "functionResponse": {
                                        "name": f_name,
                                        "response": {"name": f_name, "content": result}
                                    }
                                }]
                            })
                            continue # Next turn in loop
                        
                        # B. Handle Text Response
                        if "text" in part:
                            text = part["text"].strip()
                            
                            # Check for grounding feedback
                            grounding = candidates[0].get('groundingMetadata', {})
                            if grounding and (grounding.get('webSearchQueries') or grounding.get('groundingChunks')):
                                logger.info(f"Gemini used search grounding")
                                text = f"🌐 {text}"
                            
                            return text
                        
                        return "⚠️ Unexpected response part from Gemini"
                    
                    # Handle Errors (Surgical Fallback or Terminal)
                    elif response.status_code == 400 and gemini_tools:
                        # ... grounding fallback logic ...
                        error_data = response.json()
                        error_msg = error_data.get('error', {}).get('message', '').lower()
                        logger.warning(f"⚠️ Tool configuration issue with model '{model}': {error_msg}")
                        logger.debug(f"FULL ERROR: {response.text}")
                        
                        # Strip problematic tools and retry (internal retry)
                        if "google_maps" in error_msg:
                            gemini_tools = [t for t in gemini_tools if "google_maps" not in t]
                        else:
                            # If it's not Maps, maybe it's function_declarations or Search
                            # For safety, let's just strip all tools if it keeps failing
                            gemini_tools = []
                        
                        if not gemini_tools:
                            payload.pop("tools", None)
                        else:
                            payload["tools"] = gemini_tools
                        
                        continue # Internal retry turn
                    
                    else:
                        # Terminal error for this attempt
                        break 
                
                
            except requests.exceptions.Timeout as e:
                # Timeout means network is broken (likely DNS hang). Retrying won't help.
                # Return immediately so the worker thread exits within the 45s deadline.
                logger.error(f"⏱️ Gemini request timed out (DNS/network hang): {e}")
                return "❌ Request timed out. The AI is temporarily unreachable."
            except Exception as e:
                if attempt < max_retries:
                    logger.warning(f"⚠️ Gemini request failed: {e}. Retrying...")
                    continue
                else:
                    logger.error(f"Gemini request final failure: {e}")
                    return f"❌ Unexpected error: {str(e)[:100]}"
        
        return "❌ Failed to get response after multiple attempts."
