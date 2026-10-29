# Copyright (c) 2026 ln4cy
# This software is released under the MIT License.
# See LICENSE file in the project root for full license details.

"""Compatibility helpers for resolving symbols across the modular core package.

Existing unit tests mock attributes directly on the top-level `ai_responder` module
(e.g., `@patch('ai_responder.get_provider')`, `@patch('ai_responder.datetime')`,
`@patch('ai_responder.MeshtasticHandler')`). Resolving these through `sys.modules`
ensures 100% backward compatibility with all test mocks while keeping implementation
code cleanly modularized across `core/` and `responders/`.
"""

import sys
import time as _std_time
import datetime as _std_datetime
import logging as _std_logging
import requests as _std_requests
import config as _std_config
from config import Config as _StdConfig
from providers import get_provider as _std_get_provider
from conversation.manager import ConversationManager as _StdConversationManager
from conversation.session import SessionManager as _StdSessionManager
from mcp_client import UnifiedMCPClient as _StdUnifiedMCPClient

_default_logger = _std_logging.getLogger('AI-Responder')


def resolve_attr(name, default):
    """Resolve an attribute from the `ai_responder` module if loaded, else return default."""
    mod = sys.modules.get('ai_responder')
    if mod is not None and hasattr(mod, name):
        return getattr(mod, name)
    return default


def get_logger():
    return resolve_attr('logger', _default_logger)


def get_time():
    return resolve_attr('time', _std_time)


def get_datetime():
    return resolve_attr('datetime', _std_datetime)


def get_requests():
    return resolve_attr('requests', _std_requests)


def get_config_module():
    return resolve_attr('config', _std_config)


def get_config_cls():
    return resolve_attr('Config', _StdConfig)


def get_provider_fn():
    return resolve_attr('get_provider', _std_get_provider)


def get_conversation_manager_cls():
    return resolve_attr('ConversationManager', _StdConversationManager)


def get_session_manager_cls():
    return resolve_attr('SessionManager', _StdSessionManager)


def get_mcp_client_cls():
    return resolve_attr('UnifiedMCPClient', _StdUnifiedMCPClient)
