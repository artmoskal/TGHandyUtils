import requests
import logging
from platforms.base import AbstractTaskPlatform

logger = logging.getLogger(__name__)

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
                
        Returns:
            str: Task ID if successful, None otherwise
        """
        url = f'{self.base_url}/tasks'
        data = {
            'content': task_data['title'],
            'description': task_data['description'],
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