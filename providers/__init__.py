"""AI Provider modules."""

from .ollama import OllamaProvider
from .gemini import GeminiProvider
from .openai import OpenAIProvider
from .anthropic import AnthropicProvider

__all__ = ['OllamaProvider', 'GeminiProvider', 'OpenAIProvider', 'AnthropicProvider']


def get_provider(provider_name, config):
    """Factory function to get the appropriate provider."""
    providers = {
        'ollama': OllamaProvider,
        'local': OllamaProvider,
        'gemini': GeminiProvider,
        'openai': OpenAIProvider,
        'anthropic': AnthropicProvider
    }
    
    provider_class = providers.get(provider_name.lower())
    if not provider_class:
        raise ValueError(f"Unknown provider: {provider_name}")
    
    return provider_class(config)
