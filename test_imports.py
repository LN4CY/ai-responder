#!/usr/bin/env python3
"""Test script to verify all modules can be imported."""

import sys

def test_imports():
    """Test that all modules can be imported without errors."""
    try:
        print("Testing config module...")
        import config
        print("✅ config imported")
        
        print("\nTesting providers...")
        from providers import get_provider, OllamaProvider, GeminiProvider, OpenAIProvider, AnthropicProvider
        print("✅ providers imported")
        
        print("\nTesting conversation modules...")
        from conversation import ConversationManager, SessionManager
        print("✅ conversation modules imported")
        
        print("\nTesting meshtastic_handler...")
        from meshtastic_handler import MeshtasticHandler
        print("✅ meshtastic_handler imported")
        
        print("\n✅ All modules imported successfully!")
        return True
        
    except Exception as e:
        print(f"\n❌ Import failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_imports()
    sys.exit(0 if success else 1)
