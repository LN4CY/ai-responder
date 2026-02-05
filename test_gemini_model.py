#!/usr/bin/env python3
"""Quick test to verify Gemini model works."""

import os
import requests
import sys

# Get API key from environment
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

if not GEMINI_API_KEY:
    print("⚠️  GEMINI_API_KEY not set - skipping test")
    print("ℹ️  This test will run automatically in CI/CD where the key is available")
    sys.exit(0)  # Exit successfully

# Test the model
model = 'gemini-3-flash-preview'
url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"

payload = {
    "contents": [
        {
            "role": "user",
            "parts": [{"text": "Say 'Hello from Gemini 3 Flash!' and nothing else."}]
        }
    ]
}

print(f"Testing model: {model}")
print(f"URL: {url[:80]}...")

try:
    response = requests.post(url, json=payload, timeout=10)
    
    if response.status_code == 200:
        data = response.json()
        candidates = data.get('candidates', [])
        
        if candidates:
            text = candidates[0]['content']['parts'][0]['text'].strip()
            print(f"✅ Success! Response: {text}")
        else:
            print(f"❌ No candidates in response: {data}")
    else:
        print(f"❌ HTTP {response.status_code}")
        try:
            error_data = response.json()
            print(f"Error: {error_data}")
        except:
            print(f"Response: {response.text[:200]}")
            
except Exception as e:
    print(f"❌ Exception: {e}")
