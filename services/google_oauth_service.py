import json
from typing import Optional
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from core.logging import get_logger
from core.exceptions import OAuthError

logger = get_logger(__name__)

class GoogleOAuthService:
    def __init__(self, client_id: str, client_secret: str):
        if not client_id or not client_secret:
            self.configured = False
            logger.warning("Google OAuth credentials not configured")
            return
            
        self.configured = True
        self.client_secrets_config = {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob"]
            }
        }
        self.scopes = ['https://www.googleapis.com/auth/calendar']

    def get_authorization_url(self, user_id: int) -> str:
        """Generate OAuth URL for manual code entry."""
        if not self.configured:
            raise OAuthError("Google OAuth service not configured")
            
        from core.container import container
        
        oauth_state_manager = container.oauth_state_manager()
        state = oauth_state_manager.create_pending_request(user_id)
        
        flow = Flow.from_client_config(
            self.client_secrets_config,
            scopes=self.scopes,
            state=state
        )
        
        flow.redirect_uri = 'urn:ietf:wg:oauth:2.0:oob'
        
        authorization_url, _ = flow.authorization_url(
            access_type='offline', 
            prompt='consent'
        )
        
        logger.info(f"Generated OAuth URL for user {user_id}")
        return authorization_url

    def exchange_code_for_token(self, code: str) -> str:
        """Exchange authorization code for credentials."""
        try:
            flow = Flow.from_client_config(self.client_secrets_config, scopes=self.scopes)
            flow.redirect_uri = 'urn:ietf:wg:oauth:2.0:oob'
            flow.fetch_token(code=code)
            
            credentials = {
                'token': flow.credentials.token,
                'refresh_token': flow.credentials.refresh_token,
                'token_uri': flow.credentials.token_uri,
                'client_id': flow.credentials.client_id,
                'client_secret': flow.credentials.client_secret,
                'scopes': flow.credentials.scopes,
                'expiry': flow.credentials.expiry.isoformat() if flow.credentials.expiry else None
            }
            
            logger.info("Successfully exchanged OAuth code for tokens")
            return json.dumps(credentials)
            
        except Exception as e:
            logger.error(f"Error exchanging OAuth code: {e}")
            raise OAuthError(f"Invalid authorization code: {str(e)}")

    def refresh_token(self, credentials_json: str) -> str:
        """Refresh expired access token."""
        try:
            creds_data = json.loads(credentials_json)
            credentials = Credentials(**creds_data)
            
            if credentials.expired and credentials.refresh_token:
                credentials.refresh(Request())
                
                updated_credentials = {
                    'token': credentials.token,
                    'refresh_token': credentials.refresh_token,
                    'token_uri': credentials.token_uri,
                    'client_id': credentials.client_id,
                    'client_secret': credentials.client_secret,
                    'scopes': credentials.scopes,
                    'expiry': credentials.expiry.isoformat() if credentials.expiry else None
                }
                
                logger.info("Successfully refreshed OAuth token")
                return json.dumps(updated_credentials)
            
            return credentials_json
            
        except Exception as e:
            logger.error(f"Error refreshing token: {e}")
            raise OAuthError(f"Failed to refresh token: {str(e)}")