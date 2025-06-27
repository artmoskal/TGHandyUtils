import requests
import logging
import json
import time
from typing import Dict, Any, Optional
from platforms.base import AbstractTaskPlatform, register_platform

logger = logging.getLogger(__name__)

@register_platform('todoist')
class TodoistPlatform(AbstractTaskPlatform):
    """Implementation of the task platform interface for Todoist."""
    
    def __init__(self, api_token):
        """
        Initialize the Todoist platform.
        
        Args:
            api_token (str): The Todoist API token
        """
        self.api_token = api_token
        self.base_url = 'https://api.todoist.com/rest/v2'
        self.headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_token}'
        }
    
    def create_task(self, task_data):
        """
        Create a task in Todoist.
        
        Args:
            task_data (dict): Dictionary containing task information
                - title (str): The task title
                - description (str): The task description
                - due_time (str): The due time in ISO 8601 format
                - source_attachment (str, optional): Additional source information
                
        Returns:
            str: Task ID if successful, None otherwise
        """
        url = f'{self.base_url}/tasks'
        
        description = task_data.get('description', '')
        if 'source_attachment' in task_data and task_data['source_attachment']:
            description += f"\n\n🔗 Source: {task_data['source_attachment']}"
        
        data = {
            'content': task_data['title'],
            'description': description,
            'due_datetime': task_data['due_time']
        }
        
        try:
            response = requests.post(url, headers=self.headers, json=data)
            if response.status_code in [200, 201, 204]:
                task = response.json()
                task_id = task['id']
                logger.debug(f"Created Todoist task with ID: {task_id}")
                return task_id
            else:
                logger.error(f"Todoist API error: {response.text}")
                return None
        except Exception as e:
            logger.error(f"Todoist API error: {e}")
            return None
    
    def update_task(self, task_id, task_data):
        """
        Update a task in Todoist.
        
        Args:
            task_id (str): The ID of the task to update
            task_data (dict): Dictionary containing task information to update
                
        Returns:
            bool: True if successful, False otherwise
        """
        url = f'{self.base_url}/tasks/{task_id}'
        data = {}
        
        if 'title' in task_data:
            data['content'] = task_data['title']
        if 'description' in task_data:
            data['description'] = task_data['description']
        if 'due_time' in task_data:
            data['due_datetime'] = task_data['due_time']
        
        try:
            response = requests.post(url, headers=self.headers, json=data)
            return response.status_code in [200, 201, 204]
        except Exception as e:
            logger.error(f"Todoist API error: {e}")
            return False
    
    def delete_task(self, task_id):
        """
        Delete a task in Todoist.
        
        Args:
            task_id (str): The ID of the task to delete
                
        Returns:
            bool: True if successful, False otherwise
        """
        url = f'{self.base_url}/tasks/{task_id}'
        
        try:
            response = requests.delete(url, headers=self.headers)
            return response.status_code in [200, 201, 204]
        except Exception as e:
            logger.error(f"Todoist API error: {e}")
            return False
    
    def get_task(self, task_id):
        """
        Get a task from Todoist.
        
        Args:
            task_id (str): The ID of the task to retrieve
                
        Returns:
            dict: Task data if successful, None otherwise
        """
        url = f'{self.base_url}/tasks/{task_id}'
        
        try:
            response = requests.get(url, headers=self.headers)
            if response.status_code == 200:
                task = response.json()
                return {
                    'id': task['id'],
                    'title': task['content'],
                    'description': task.get('description', ''),
                    'due_time': task.get('due', {}).get('datetime', '')
                }
            return None
        except Exception as e:
            logger.error(f"Todoist API error: {e}")
            return None
    
    def get_task_url(self, task_id: str) -> str:
        """Generate a direct URL to a Todoist task.
        
        Args:
            task_id: The ID of the task
            
        Returns:
            Direct URL to the task
        """
        return f"https://todoist.com/showTask?id={task_id}"
    
    def upload_file(self, file_data: bytes, file_name: str) -> dict:
        """Upload a file to Todoist using REST API v2.
        
        Args:
            file_data: File data as bytes
            file_name: Name of the file
            
        Returns:
            Upload result with file_url or None if failed
        """
        upload_url = 'https://api.todoist.com/api/v1/uploads'
        
        files = {
            'file': (file_name, file_data, 'image/jpeg')
        }
        
        headers = {
            'Authorization': f'Bearer {self.api_token}'
        }
        
        try:
            response = requests.post(upload_url, headers=headers, files=files)
            if response.status_code in [200, 201]:
                result = response.json()
                logger.debug(f"Successfully uploaded file to Todoist: {file_name}")
                return result
            else:
                logger.error(f"Todoist file upload error: {response.status_code} - {response.text}")
                return None
        except Exception as e:
            logger.error(f"Error uploading file to Todoist: {e}")
            return None
    
    def add_comment_with_attachment(self, task_id: str, content: str, file_upload_result: dict) -> bool:
        """Add a comment with file attachment to a Todoist task using REST API v2.
        
        Args:
            task_id: The ID of the task
            content: Comment content
            file_upload_result: Result from upload_file method containing file_url
            
        Returns:
            True if successful, False otherwise
        """
        url = f'{self.base_url}/comments'
        
        # Create attachment object from upload result
        attachment = {
            'resource_type': 'file',
            'file_url': file_upload_result.get('file_url'),
            'file_name': file_upload_result.get('file_name'),
            'file_type': file_upload_result.get('file_type', 'image/jpeg'),
            'file_size': file_upload_result.get('file_size')
        }
        
        data = {
            'task_id': task_id,
            'content': content,
            'attachment': attachment
        }
        
        try:
            response = requests.post(url, headers=self.headers, json=data)
            if response.status_code in [200, 201, 204]:
                logger.debug(f"Successfully added comment with attachment to task {task_id}")
                return True
            else:
                logger.error(f"Todoist comment API error: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            logger.error(f"Error adding comment with attachment to Todoist task: {e}")
            return False
    
    def attach_screenshot(self, task_id: str, image_data: bytes, file_name: str) -> bool:
        """Attach a screenshot to a Todoist task.
        
        Args:
            task_id: The ID of the task
            image_data: Screenshot data
            file_name: Name of the file
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Upload file to Todoist first
            file_upload_result = self.upload_file(image_data, file_name)
            if not file_upload_result:
                logger.warning(f"Failed to upload screenshot to Todoist, adding text comment instead")
                # Fallback to text comment
                url = f'{self.base_url}/comments'
                data = {
                    'task_id': task_id,
                    'content': f"📸 Screenshot processed: {file_name}\n\n*Note: Screenshot analysis was used to create this task but file upload failed.*"
                }
                response = requests.post(url, headers=self.headers, json=data)
                return response.status_code in [200, 201, 204]
            
            # Add comment with file attachment
            return self.add_comment_with_attachment(
                task_id,
                "📸 Screenshot attached",
                file_upload_result
            )
            
        except Exception as e:
            logger.error(f"Error attaching screenshot to Todoist task: {e}")
            return False
    
    def get_token_from_settings(self, platform_settings: Dict[str, Any]) -> Optional[str]:
        """Extract Todoist token from settings."""
        return platform_settings.get('todoist_token')
    
    def is_configured(self, platform_settings: Dict[str, Any]) -> bool:
        """Check if Todoist is configured."""
        return bool(platform_settings.get('todoist_token'))
    
    @classmethod
    def is_configured_static(cls, platform_settings: Dict[str, Any]) -> bool:
        """Check if Todoist is configured without instantiation."""
        return bool(platform_settings.get('todoist_token'))