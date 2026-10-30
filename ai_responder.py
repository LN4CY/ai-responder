#!/usr/bin/env python3
# Copyright (c) 2026 ln4cy
# This software is released under the MIT License.
# See LICENSE file in the project root for full license details.
"""
AI Responder for Meshtastic & MeshCore

A modular AI assistant that responds to messages on Meshtastic or MeshCore mesh
networks. Each protocol runs as an independent responder class (`MeshtasticAIResponder`
or `MeshCoreAIResponder`) sharing a common base architecture (`core/`).

Author: AI Responder Team
License: MIT
"""

import os
import time
import json
import logging
import threading
import sys
import re
import itertools
import requests
import pathlib
import datetime
from concurrent.futures import ThreadPoolExecutor

import config
from config import (
    Config,
    INTERFACE_TYPE,
    SERIAL_PORT,
    MESHTASTIC_HOST,
    MESHTASTIC_PORT,
    ENV_ADMIN_NODE_ID,
    MESHTASTIC_ADMIN_NODE_ID,
    MESHCORE_ADMIN_NODE_ID,
    ALLOWED_CHANNELS,
    MESHTASTIC_ALLOWED_CHANNELS,
    MESHCORE_ALLOWED_CHANNELS,
    AI_PROVIDER,
    HEALTH_CHECK_ACTIVITY_TIMEOUT,
)
from providers import get_provider
from conversation.manager import ConversationManager
from conversation.session import SessionManager
from meshtastic_handler import MeshtasticHandler
from meshcore_handler import MeshCoreHandler
from mcp_client import UnifiedMCPClient
from mcp_server_meshtastic import init_meshtastic_mcp
from mcp_server_meshcore import init_meshcore_mcp

from core import BaseAIResponder
from responders import MeshtasticAIResponder, MeshCoreAIResponder

# Logging setup
log_level = os.environ.get('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    force=True
)
logger = logging.getLogger('AI-Responder')

# Set logging level for handlers based on global log_level
logging.getLogger('meshtastic_handler').setLevel(getattr(logging, log_level, logging.INFO))
logging.getLogger('meshcore_handler').setLevel(getattr(logging, log_level, logging.INFO))

# Suppress noisy httpx INFO logs ("HTTP Request: POST ...") — only errors are useful
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)

__version__ = "2.2.0"

# Backward-compatible alias for Meshtastic AI Responder
AIResponder = MeshtasticAIResponder


def create_responder(mesh_type=None, history_dir=None):
    """
    Factory function to instantiate the protocol-specific AI Responder.

    Args:
        mesh_type: 'meshtastic' or 'meshcore' (defaults to config.MESH_TYPE)
        history_dir: Optional directory for conversation history persistence

    Returns:
        BaseAIResponder: Instance of `MeshtasticAIResponder` or `MeshCoreAIResponder`
    """
    resolved_type = (mesh_type or getattr(config, 'MESH_TYPE', 'meshtastic')).strip().lower()
    if resolved_type == 'meshcore':
        return MeshCoreAIResponder(history_dir=history_dir)
    return MeshtasticAIResponder(history_dir=history_dir)


__all__ = [
    "__version__",
    "AIResponder",
    "BaseAIResponder",
    "MeshtasticAIResponder",
    "MeshCoreAIResponder",
    "create_responder",
    "Config",
    "MeshtasticHandler",
    "MeshCoreHandler",
    "ConversationManager",
    "SessionManager",
    "UnifiedMCPClient",
    "get_provider",
    "config",
    "logger",
    "time",
    "datetime",
    "requests",
    "json",
    "os",
    "sys",
    "re",
    "itertools",
    "threading",
    "pathlib",
    "ThreadPoolExecutor",
    "init_meshtastic_mcp",
    "init_meshcore_mcp",
    "INTERFACE_TYPE",
    "SERIAL_PORT",
    "MESHTASTIC_HOST",
    "MESHTASTIC_PORT",
    "ENV_ADMIN_NODE_ID",
    "MESHTASTIC_ADMIN_NODE_ID",
    "MESHCORE_ADMIN_NODE_ID",
    "ALLOWED_CHANNELS",
    "MESHTASTIC_ALLOWED_CHANNELS",
    "MESHCORE_ALLOWED_CHANNELS",
    "AI_PROVIDER",
    "HEALTH_CHECK_ACTIVITY_TIMEOUT",
]


if __name__ == "__main__":
    responder = create_responder()
    responder.connect()
