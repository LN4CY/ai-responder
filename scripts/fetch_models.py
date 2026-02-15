import os
import sys
import json
import urllib.request
import urllib.error

def fetch_http(url, headers=None):
    try:
        req = urllib.request.Request(url, headers=headers or {})
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.read().decode('utf-8')
    except:
        return None

def fetch_ollama_models():
    data = fetch_http("http://localhost:11434/api/tags")
    if data:
        try:
            models = json.loads(data).get('models', [])
            return [m['name'] for m in models]
        except:
            pass
    return ["llama3.2:1b", "llama3.1:8b", "mistral"]

def fetch_gemini_models(api_key):
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    data = fetch_http(url)
    if data:
        try:
            models = json.loads(data).get('models', [])
            return [m['name'].replace('models/', '') for m in models if 'generateContent' in m.get('supportedGenerationMethods', [])]
        except Exception as e:
            return [f"Parsing Error: {str(e)}"]
    return ["gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash"]

def fetch_openai_models(api_key):
    headers = {"Authorization": f"Bearer {api_key}"}
    data = fetch_http("https://api.openai.com/v1/models", headers=headers)
    if data:
        try:
            models = json.loads(data).get('data', [])
            return sorted([m['id'] for m in models if 'gpt' in m['id']])
        except Exception as e:
            return [f"Parsing Error: {str(e)}"]
    return ["gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo"]

def fetch_anthropic_models(api_key):
    # Anthropic doesn't have a simple GET endpoint for models without complex headers/versions
    return ["claude-3-5-sonnet-latest", "claude-3-haiku-20240307", "claude-3-opus-20240229"]

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps([]))
        sys.exit(0)

    provider = sys.argv[1].lower()
    api_key = sys.argv[2] if len(sys.argv) > 2 else ""

    models = []
    if provider == "ollama":
        models = fetch_ollama_models()
    elif provider == "gemini":
        models = fetch_gemini_models(api_key)
    elif provider == "openai":
        models = fetch_openai_models(api_key)
    elif provider == "anthropic":
        models = fetch_anthropic_models(api_key)

    print(json.dumps(models))
