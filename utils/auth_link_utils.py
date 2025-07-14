"""Utilities for encoding/decoding auth request data in bot links."""

import base64
import json
from typing import Optional, Dict
from datetime import datetime

from core.logging import get_logger

logger = get_logger(__name__)


def encode_auth_request_data(requester_user_id: int, platform_type: str, 
                            recipient_name: str, requester_name: str) -> str:
    """Encode auth request data for bot link.
    
    Args:
        requester_user_id: ID of user requesting authentication
        platform_type: Type of platform (todoist/trello/google_calendar)
        recipient_name: Name for the account
        requester_name: First name of requester
        
    Returns:
        Base64 encoded string safe for URLs
    """
    # Shorten platform type to save space
    platform_map = {
        'todoist': 't',
        'trello': 'r', 
        'google_calendar': 'g'
    }
    
    data = {
        'r': requester_user_id,                          # requester user_id
        'p': platform_map.get(platform_type, 't'),      # platform shortcode
        'n': recipient_name[:30],                        # account name (truncated)
        'u': requester_name[:20],                        # requester's first name
        't': int(datetime.utcnow().timestamp())          # timestamp for expiry
    }
    
    json_str = json.dumps(data, separators=(',', ':'))
    encoded = base64.urlsafe_b64encode(json_str.encode()).decode().rstrip('=')
    
    logger.debug(f"Encoded auth request: {len(encoded)} chars")
    return encoded


def decode_auth_request_data(encoded: str) -> Optional[Dict]:
    """Decode auth request data from bot link.
    
    Args:
        encoded: Base64 encoded string from bot link
        
    Returns:
        Dict with auth request data or None if invalid/expired
    """
    try:
        # Add padding if needed
        padding = 4 - len(encoded) % 4
        if padding != 4:
            encoded += '=' * padding
        
        json_str = base64.urlsafe_b64decode(encoded).decode()
        data = json.loads(json_str)
        
        # Check if expired (24 hours)
        timestamp = data.get('t', 0)
        if datetime.utcnow().timestamp() - timestamp > 86400:
            logger.warning("Auth link expired")
            return None
        
        # Expand platform type
        platform_map = {
            't': 'todoist',
            'r': 'trello', 
            'g': 'google_calendar'
        }
        
        return {
            'requester_user_id': data['r'],
            'platform_type': platform_map.get(data['p'], 'todoist'),
            'recipient_name': data['n'],
            'requester_name': data.get('u', 'User')
        }
        
    except Exception as e:
        logger.error(f"Failed to decode auth request data: {e}")
        return None