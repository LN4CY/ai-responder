"""Base AI provider class."""

from abc import ABC, abstractmethod
import logging

logger = logging.getLogger(__name__)


class BaseProvider(ABC):
    """Abstract base class for AI providers."""
    
    def __init__(self, config):
        """Initialize provider with configuration."""
        self.config = config
    
    @abstractmethod
    def get_response(self, prompt, history=None):
        """
        Get AI response for the given prompt.
        
        Args:
            prompt: User's input text
            history: List of previous messages [{'role': 'user/assistant', 'content': '...'}]
        
        Returns:
            str: AI response text or error message
        """
        pass
    
    @property
    @abstractmethod
    def name(self):
        """Provider name."""
        pass
    
    def format_error(self, status_code, error_msg):
        """Format error message for user."""
        if status_code == 429 or 'quota' in error_msg.lower() or 'rate' in error_msg.lower():
            return "⏱️ Rate limit reached. Try again in a few minutes."
        elif status_code == 400:
            return f"❌ Invalid request: {error_msg[:100]}"
        elif status_code == 401 or status_code == 403:
            return "🔒 API key issue. Contact admin."
        elif status_code == 500 or status_code == 503:
            return f"🔧 {self.name} service error ({status_code}): {error_msg}"
        else:
            return f"❌ {self.name} error ({status_code}): {error_msg[:100]}"
