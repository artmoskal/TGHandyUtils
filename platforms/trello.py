import requests
import logging
from platforms.base import AbstractTaskPlatform, register_platform
import urllib.parse
from datetime import datetime
from typing import Optional, Dict, Any
from helpers.error_helpers import with_timeout_and_retry, PlatformError, PlatformAuthError, PlatformConfigError
from helpers.constants import HttpConstants

logger = logging.getLogger(__name__)

@register_platform('trello')
class TrelloPlatform(AbstractTaskPlatform):
    """Implementation of the task platform interface for Trello."""
    
    def __init__(self, api_token):
        """
        Initialize the Trello platform.
        
        Args:
            api_token (str): The Trello API token in format 'key:token'
                             where key is the API key and token is the user token
        """
        # Split the API token into key and token
        try:
            self.api_key, self.token = api_token.split(':')
        except ValueError:
            # Only log error for non-dummy tokens to avoid noise in logs
            if api_token != "dummy":
                logger.error("Invalid Trello API token format. Expected 'key:token'")
            self.api_key = ''
            self.token = ''
            
        self.base_url = 'https://api.trello.com/1'
        # Default board and list IDs (should be configurable in user settings)
        self.default_board_id = None
        self.default_list_id = None
    
    def set_default_board(self, board_id):
        """Set the default board ID."""
        self.default_board_id = board_id
    
    def set_default_list(self, list_id):
        """Set the default list ID."""
        self.default_list_id = list_id
    
    def _get_auth_params(self):
        """Get the authentication parameters for Trello API."""
        return {
            'key': self.api_key,
            'token': self.token
        }
    
    def get_boards(self):
        """Get all boards accessible to the user."""
        url = f'{self.base_url}/members/me/boards'
        params = self._get_auth_params()
        
        try:
            response = requests.get(url, params=params)
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"Trello API error: {response.text}")
                return []
        except Exception as e:
            logger.error(f"Trello API error: {e}")
            return []
    
    def get_lists(self, board_id):
        """Get all lists in a board."""
        url = f'{self.base_url}/boards/{board_id}/lists'
        params = self._get_auth_params()
        
        try:
            response = requests.get(url, params=params)
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"Trello API error: {response.text}")
                return []
        except Exception as e:
            logger.error(f"Trello API error: {e}")
            return []
    
    @with_timeout_and_retry(max_retries=HttpConstants.MAX_RETRIES)
    def create_task(self, task_data):
        """
        Create a task (card) in Trello.
        
        Args:
            task_data (dict): Dictionary containing task information
                - title (str): The task title
                - description (str): The task description
                - due_time (str): The due time in ISO 8601 format
                - board_id (str, optional): The board ID
                - list_id (str, optional): The list ID
                - source_attachment (str, optional): Additional source information
                
        Returns:
            str: Task ID if successful, None otherwise
            
        Raises:
            PlatformConfigError: If required settings are missing
        """
        url = f'{self.base_url}/cards'
        
        # Use provided board/list IDs or fall back to defaults
        board_id = task_data.get('board_id', self.default_board_id)
        list_id = task_data.get('list_id', self.default_list_id)
        
        if not list_id:
            raise PlatformConfigError("trello", "No list ID configured. Please check your board permissions in Settings.")
        
        description = task_data.get('description', '')
        if 'source_attachment' in task_data and task_data['source_attachment']:
            description += f"\n\n🔗 Source: {task_data['source_attachment']}"
        
        params = self._get_auth_params()
        params.update({
            'idList': list_id,
            'name': task_data['title'],
            'desc': description
        })
        
        # Convert ISO due time to Trello format if provided
        if 'due_time' in task_data and task_data['due_time']:
            params['due'] = task_data['due_time']
        
        response = requests.post(url, params=params, timeout=HttpConstants.HTTP_TIMEOUT)
        response.raise_for_status()
        
        if response.status_code in [200, 201]:
            card = response.json()
            logger.debug(f"Created Trello card with ID: {card['id']}")
            return card['id']
        else:
            raise PlatformError("trello", f"Unexpected response: {response.text}")
    
    def update_task(self, task_id, task_data):
        """
        Update a task (card) in Trello.
        
        Args:
            task_id (str): The ID of the task to update
            task_data (dict): Dictionary containing task information to update
                
        Returns:
            bool: True if successful, False otherwise
        """
        url = f'{self.base_url}/cards/{task_id}'
        params = self._get_auth_params()
        
        if 'title' in task_data:
            params['name'] = task_data['title']
        if 'description' in task_data:
            params['desc'] = task_data['description']
        if 'due_time' in task_data:
            params['due'] = task_data['due_time']
        
        try:
            response = requests.put(url, params=params)
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Trello API error: {e}")
            return False
    
    @with_timeout_and_retry(max_retries=HttpConstants.MAX_RETRIES)
    def delete_task(self, task_id):
        """
        Delete a task (card) in Trello.
        
        Args:
            task_id (str): The ID of the task to delete
                
        Returns:
            bool: True if successful, False otherwise
        """
        url = f'{self.base_url}/cards/{task_id}'
        params = self._get_auth_params()
        
        response = requests.delete(url, params=params, timeout=HttpConstants.HTTP_TIMEOUT)
        response.raise_for_status()
        return response.status_code in [200, 404]  # 404 is acceptable (already deleted)
    
    def get_task(self, task_id):
        """
        Get a task (card) from Trello.
        
        Args:
            task_id (str): The ID of the task to retrieve
                
        Returns:
            dict: Task data if successful, None otherwise
        """
        url = f'{self.base_url}/cards/{task_id}'
        params = self._get_auth_params()
        
        try:
            response = requests.get(url, params=params)
            if response.status_code == 200:
                card = response.json()
                return {
                    'id': card['id'],
                    'title': card['name'],
                    'description': card.get('desc', ''),
                    'due_time': card.get('due', '')
                }
            return None
        except Exception as e:
            logger.error(f"Trello API error: {e}")
            return None
    
    def add_attachment_to_card(self, card_id: str, file_data: bytes, file_name: str) -> bool:
        """Add a file attachment to a Trello card.
        
        Args:
            card_id: The ID of the card to attach to
            file_data: The file data as bytes
            file_name: Name of the file
            
        Returns:
            True if successful, False otherwise
        """
        url = f'{self.base_url}/cards/{card_id}/attachments'
        
        params = self._get_auth_params()
        params['name'] = file_name
        
        files = {
            'file': (file_name, file_data, 'image/jpeg')
        }
        
        try:
            response = requests.post(url, params=params, files=files)
            if response.status_code in [200, 201]:
                logger.debug(f"Successfully added attachment {file_name} to card {card_id}")
                return True
            else:
                logger.error(f"Failed to add attachment to Trello card: {response.text}")
                return False
        except Exception as e:
            logger.error(f"Error adding attachment to Trello card: {e}")
            return False
    
    def attach_screenshot(self, task_id: str, image_data: bytes, file_name: str) -> bool:
        """Attach a screenshot to a Trello card.
        
        Args:
            task_id: The ID of the card
            image_data: Screenshot data
            file_name: Name of the file
            
        Returns:
            True if successful, False otherwise
        """
        logger.info(f"Trello attach_screenshot called: task_id={task_id}, file_name={file_name}, data_size={len(image_data) if image_data else 0}")
        return self.add_attachment_to_card(task_id, image_data, file_name)
    
    def get_task_url(self, task_id: str) -> str:
        """Generate a direct URL to a Trello card.
        
        Args:
            task_id: The ID of the card
            
        Returns:
            Direct URL to the card
        """
        return f"https://trello.com/c/{task_id}"
    
    def get_token_from_settings(self, platform_settings: Dict[str, Any]) -> Optional[str]:
        """Extract Trello token from settings."""
        trello_key = platform_settings.get('trello_key')
        trello_token = platform_settings.get('trello_token')
        if trello_key and trello_token:
            return f"{trello_key}:{trello_token}"
        return None
    
    def is_configured(self, platform_settings: Dict[str, Any]) -> bool:
        """Check if Trello is configured."""
        return bool(platform_settings.get('trello_key') and 
                   platform_settings.get('trello_token') and
                   platform_settings.get('trello_board_id') and
                   platform_settings.get('trello_list_id'))
    
    @classmethod
    def is_configured_static(cls, platform_settings: Dict[str, Any]) -> bool:
        """Check if Trello is configured without instantiation."""
        return bool(platform_settings.get('trello_key') and 
                   platform_settings.get('trello_token') and
                   platform_settings.get('trello_board_id') and
                   platform_settings.get('trello_list_id'))