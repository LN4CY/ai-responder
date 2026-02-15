import os
import sys
import json
import urllib.request
import urllib.error

def post_http(url, body, headers=None):
    try:
        data = json.dumps(body).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers=headers or {}, method='POST')
        with urllib.request.urlopen(req, timeout=10) as response:
            return True, response.read().decode('utf-8')
    except urllib.error.HTTPError as e:
        return False, f"HTTP Error {e.code}: {e.read().decode('utf-8')}"
    except Exception as e:
        return False, f"Connection failed: {str(e)}"

def validate_ollama(model):
    url = "http://localhost:11434/api/chat"
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "hi"}],
        "stream": False
    }
    return post_http(url, body)

def validate_gemini(model, api_key):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    body = {"contents": [{"parts": [{"text": "hi"}]}]}
    success, resp = post_http(url, body)
    if success:
        return True, "Gemini connection successful."
    return False, f"Gemini error: {resp}"

def validate_openai(model, api_key):
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 5
    }
    success, resp = post_http(url, body, headers)
    if success:
        return True, "OpenAI connection successful."
    return False, f"OpenAI error: {resp}"

def validate_anthropic(model, api_key):
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json"
    }
    body = {
        "model": model,
        "max_tokens": 5,
        "messages": [{"role": "user", "content": "hi"}]
    }
    success, resp = post_http(url, body, headers)
    if success:
        return True, "Anthropic connection successful."
    return False, f"Anthropic error: {resp}"

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(json.dumps({"success": False, "message": "Missing arguments."}))
        sys.exit(1)

    provider = sys.argv[1].lower()
    model = sys.argv[2]
    api_key = sys.argv[3] if len(sys.argv) > 2 else ""

    success = False
    message = "Unknown provider."

    if provider == "ollama":
        success, message = validate_ollama(model)
    elif provider == "gemini":
        success, message = validate_gemini(model, api_key)
    elif provider == "openai":
        success, message = validate_openai(model, api_key)
    elif provider == "anthropic":
        success, message = validate_anthropic(model, api_key)

    print(json.dumps({"success": success, "message": message}))
