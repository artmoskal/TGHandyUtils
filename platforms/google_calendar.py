import json
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from platforms.base import AbstractTaskPlatform, register_platform
from core.exceptions import PlatformError, OAuthError
from core.logging import get_logger

logger = get_logger(__name__)

@register_platform('google_calendar')
class GoogleCalendarPlatform(AbstractTaskPlatform):
    def __init__(self, credentials_json: str):
        """Initialize with OAuth credentials and auto-refresh if needed."""
        try:
            creds_data = json.loads(credentials_json)
            
            # Parse expiry string to datetime object if needed
            if 'expiry' in creds_data and isinstance(creds_data['expiry'], str):
                from datetime import datetime
                creds_data['expiry'] = datetime.fromisoformat(creds_data['expiry'].replace('Z', '+00:00'))
            
            self.credentials = Credentials(**creds_data)
            
            # Auto-refresh if expired
            if self.credentials.expired and self.credentials.refresh_token:
                logger.info("Token expired, refreshing...")
                self.credentials.refresh(Request())
                self._credentials_refreshed = True
            else:
                self._credentials_refreshed = False
            
            self.service = build('calendar', 'v3', credentials=self.credentials)
            logger.info("Google Calendar platform initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize Google Calendar platform: {e}")
            raise PlatformError(f"Invalid Google Calendar credentials: {str(e)}")

    def create_task(self, task_data: Dict[str, Any]) -> Optional[str]:
        """Create calendar event from task data."""
        try:
            platform_config = task_data.get('platform_config', {})
            if isinstance(platform_config, str):
                platform_config = json.loads(platform_config)
            
            calendar_id = platform_config.get('calendar_id', 'primary')
            
            # Parse due time
            due_time = task_data['due_time']
            if isinstance(due_time, str):
                start_dt = datetime.fromisoformat(due_time.replace('Z', '+00:00'))
            else:
                start_dt = due_time
            
            end_dt = start_dt + timedelta(hours=1)
            
            event = {
                'summary': task_data['title'],
                'description': task_data.get('description', ''),
                'start': {
                    'dateTime': start_dt.isoformat(),
                    'timeZone': task_data.get('timezone', 'UTC')
                },
                'end': {
                    'dateTime': end_dt.isoformat(),
                    'timeZone': task_data.get('timezone', 'UTC')
                },
                'reminders': {
                    'useDefault': False,
                    'overrides': [
                        {'method': 'popup', 'minutes': 15}
                    ]
                }
            }
            
            if task_data.get('location'):
                event['location'] = task_data['location']
            
            created_event = self.service.events().insert(
                calendarId=calendar_id,
                body=event
            ).execute()
            
            event_id = created_event.get('id')
            logger.info(f"Created Google Calendar event: {event_id}")
            return event_id
            
        except HttpError as e:
            if e.resp.status == 401:
                logger.error("Google Calendar authentication failed")
                raise OAuthError("Google Calendar authentication expired. Please reconnect your account.")
            elif e.resp.status == 403:
                logger.error("Google Calendar access forbidden")
                raise PlatformError("Access to Google Calendar denied. Please check permissions.")
            else:
                logger.error(f"Google Calendar API error: {e}")
                raise PlatformError(f"Failed to create calendar event: {str(e)}")
        except Exception as e:
            logger.error(f"Error creating Google Calendar event: {e}")
            raise PlatformError(f"Failed to create calendar event: {str(e)}")

    def update_task(self, task_id: str, task_data: Dict[str, Any]) -> bool:
        """Update calendar event."""
        try:
            platform_config = task_data.get('platform_config', {})
            if isinstance(platform_config, str):
                platform_config = json.loads(platform_config)
            
            calendar_id = platform_config.get('calendar_id', 'primary')
            
            # Get existing event
            event = self.service.events().get(
                calendarId=calendar_id,
                eventId=task_id
            ).execute()
            
            # Update fields
            if 'title' in task_data:
                event['summary'] = task_data['title']
            if 'description' in task_data:
                event['description'] = task_data['description']
            if 'due_time' in task_data:
                due_time = task_data['due_time']
                if isinstance(due_time, str):
                    start_dt = datetime.fromisoformat(due_time.replace('Z', '+00:00'))
                else:
                    start_dt = due_time
                
                end_dt = start_dt + timedelta(hours=1)
                
                event['start'] = {
                    'dateTime': start_dt.isoformat(),
                    'timeZone': task_data.get('timezone', 'UTC')
                }
                event['end'] = {
                    'dateTime': end_dt.isoformat(),
                    'timeZone': task_data.get('timezone', 'UTC')
                }
            
            self.service.events().update(
                calendarId=calendar_id,
                eventId=task_id,
                body=event
            ).execute()
            
            logger.info(f"Updated Google Calendar event: {task_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error updating Google Calendar event {task_id}: {e}")
            return False

    def delete_task(self, task_id: str) -> bool:
        """Delete calendar event."""
        try:
            self.service.events().delete(
                calendarId='primary',
                eventId=task_id
            ).execute()
            
            logger.info(f"Deleted Google Calendar event: {task_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error deleting Google Calendar event {task_id}: {e}")
            return False

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get calendar event."""
        try:
            event = self.service.events().get(
                calendarId='primary',
                eventId=task_id
            ).execute()
            
            return {
                'id': event.get('id'),
                'title': event.get('summary', ''),
                'description': event.get('description', ''),
                'start': event.get('start', {}).get('dateTime'),
                'end': event.get('end', {}).get('dateTime'),
                'location': event.get('location', ''),
                'url': event.get('htmlLink', '')
            }
            
        except Exception as e:
            logger.error(f"Error getting Google Calendar event {task_id}: {e}")
            return None

    def get_task_url(self, task_id: str) -> str:
        """Get direct URL to calendar event."""
        try:
            event = self.service.events().get(
                calendarId='primary',
                eventId=task_id
            ).execute()
            
            return event.get('htmlLink', f"https://calendar.google.com/calendar/event?eid={task_id}")
            
        except Exception as e:
            logger.error(f"Error getting URL for Google Calendar event {task_id}: {e}")
            return f"https://calendar.google.com/calendar/event?eid={task_id}"

    def attach_screenshot(self, task_id: str, image_data: bytes, file_name: str) -> bool:
        """Google Calendar doesn't support file attachments directly."""
        logger.warning("Google Calendar doesn't support direct file attachments")
        return False

    def get_token_from_settings(self, platform_settings: Dict[str, Any]) -> Optional[str]:
        """Extract OAuth credentials from settings."""
        return platform_settings.get('credentials')

    def is_configured(self, platform_settings: Dict[str, Any]) -> bool:
        """Check if Google Calendar is configured."""
        credentials = platform_settings.get('credentials')
        if not credentials:
            return False
        
        try:
            creds_data = json.loads(credentials)
            return all(key in creds_data for key in ['token', 'refresh_token', 'client_id', 'client_secret'])
        except (json.JSONDecodeError, TypeError):
            return False

    @classmethod
    def is_configured_static(cls, platform_settings: Dict[str, Any]) -> bool:
        """Check if Google Calendar is configured without instantiation."""
        credentials = platform_settings.get('credentials')
        if not credentials:
            return False
        
        try:
            creds_data = json.loads(credentials)
            return all(key in creds_data for key in ['token', 'refresh_token', 'client_id', 'client_secret'])
        except (json.JSONDecodeError, TypeError):
            return False

    def get_updated_credentials(self) -> Optional[str]:
        """Get updated credentials if they were refreshed."""
        if self._credentials_refreshed:
            credentials = {
                'token': self.credentials.token,
                'refresh_token': self.credentials.refresh_token,
                'token_uri': self.credentials.token_uri,
                'client_id': self.credentials.client_id,
                'client_secret': self.credentials.client_secret,
                'scopes': self.credentials.scopes,
                'expiry': self.credentials.expiry.isoformat() if self.credentials.expiry else None
            }
            return json.dumps(credentials)
        return None