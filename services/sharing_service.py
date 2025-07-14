from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
from core.logging import get_logger
from models.shared_authorization import SharedAuthorization
from models.auth_request import AuthRequest
from core.exceptions import SharingError

logger = get_logger(__name__)

class SharingService:
    def __init__(self, repository, user_service, config=None):
        self.repository = repository
        self.user_service = user_service
        self.config = config

    def create_shared_authorization(self, owner_user_id: int, grantee_user_id: int, 
                                  owner_recipient_id: int, permission_level: str = 'use') -> int:
        """Create shared authorization for existing account."""
        try:
            if owner_user_id == grantee_user_id:
                raise ValueError("Cannot share account with yourself")
            
            # Check if authorization already exists
            existing = self.repository.get_shared_authorization(
                owner_user_id, grantee_user_id, owner_recipient_id
            )
            if existing:
                raise ValueError("Authorization already exists for this account")
            
            auth_id = self.repository.create_shared_authorization(
                owner_user_id=owner_user_id,
                grantee_user_id=grantee_user_id,
                owner_recipient_id=owner_recipient_id,
                permission_level=permission_level
            )
            
            logger.info(f"Created shared authorization {auth_id}: {owner_user_id} -> {grantee_user_id}")
            return auth_id
            
        except Exception as e:
            logger.error(f"Error creating shared authorization: {e}")
            raise

    def get_shared_authorizations_by_owner(self, owner_user_id: int) -> List[SharedAuthorization]:
        """Get all authorizations created by user (as owner)."""
        return self.repository.get_shared_authorizations_by_owner(owner_user_id)

    def get_shared_authorizations_by_grantee(self, grantee_user_id: int) -> List[SharedAuthorization]:
        """Get all authorizations granted to user (as grantee)."""
        return self.repository.get_shared_authorizations_by_grantee(grantee_user_id)

    def accept_shared_authorization(self, auth_id: int, grantee_user_id: int) -> bool:
        """Accept shared authorization and create shared recipient."""
        try:
            auth = self.repository.get_shared_authorization_by_id(auth_id)
            if not auth or auth.grantee_user_id != grantee_user_id:
                raise ValueError("Authorization not found or not for this user")
            
            if auth.status != 'pending':
                raise ValueError(f"Authorization is {auth.status}, cannot accept")
            
            # Update authorization status
            success = self.repository.update_shared_authorization_status(auth_id, 'accepted')
            if not success:
                raise ValueError("Failed to update authorization status")
            
            # Create shared recipient for grantee
            owner_recipient = self.repository.get_recipient_by_id(auth.owner_user_id, auth.owner_recipient_id)
            if not owner_recipient:
                raise ValueError("Owner recipient not found")
            
            shared_recipient_id = self.repository.add_shared_recipient(
                user_id=grantee_user_id,
                name=f"{owner_recipient.name} (Shared)",
                platform_type=owner_recipient.platform_type,
                shared_authorization_id=auth_id
            )
            
            logger.info(f"Accepted shared authorization {auth_id}, created recipient {shared_recipient_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error accepting shared authorization: {e}")
            return False

    def revoke_shared_authorization(self, auth_id: int, owner_user_id: int) -> bool:
        """Revoke shared authorization."""
        try:
            auth = self.repository.get_shared_authorization_by_id(auth_id)
            if not auth or auth.owner_user_id != owner_user_id:
                raise ValueError("Authorization not found or not owned by user")
            
            return self.repository.update_shared_authorization_status(auth_id, 'revoked')
            
        except Exception as e:
            logger.error(f"Error revoking shared authorization: {e}")
            return False

    def decline_shared_authorization(self, auth_id: int, grantee_user_id: int) -> bool:
        """Decline shared authorization."""
        try:
            auth = self.repository.get_shared_authorization_by_id(auth_id)
            if not auth or auth.grantee_user_id != grantee_user_id:
                raise ValueError("Authorization not found or not for this user")
            
            return self.repository.update_shared_authorization_status(auth_id, 'declined')
            
        except Exception as e:
            logger.error(f"Error declining shared authorization: {e}")
            return False

    # Authentication request workflow methods
    def create_auth_request(self, requester_user_id: int, target_username: str, 
                          platform_type: str, recipient_name: str) -> Dict[str, Any]:
        """Create auth request or return bot link data."""
        try:
            # Clean username
            clean_username = target_username.strip().lstrip('@')
            
            # Validate platform type
            if platform_type not in ['todoist', 'trello', 'google_calendar']:
                raise ValueError(f"Invalid platform type: {platform_type}")
            
            # Check if user exists using the user service
            target_user_id = self.user_service.find_user_by_username(clean_username)
            
            if target_user_id:
                # EXISTING USER - Create auth request
                if target_user_id == requester_user_id:
                    # Check if self-auth requests are allowed (for testing)
                    if not (self.config and getattr(self.config, 'ALLOW_SELF_AUTH_REQUESTS', False)):
                        raise ValueError("❌ You can't request authentication from yourself!")
                
                # Create the auth request
                expires_at = datetime.now() + timedelta(hours=24)
                auth_request = self.repository.create_auth_request(
                    requester_user_id=requester_user_id,
                    target_user_id=target_user_id,
                    platform_type=platform_type,
                    recipient_name=recipient_name,
                    expires_at=expires_at
                )
                
                logger.info(f"Created auth request {auth_request.id} for user @{clean_username} (ID: {target_user_id})")
                
                return {
                    'status': 'created',
                    'auth_request': auth_request,
                    'target_user_id': target_user_id,
                    'target_username': clean_username
                }
            else:
                # NEW USER - Return info for bot link
                logger.info(f"User @{clean_username} not found, will generate bot link")
                
                return {
                    'status': 'user_not_found',
                    'target_username': clean_username
                }
            
        except Exception as e:
            logger.error(f"Error in create_auth_request: {e}")
            raise

    def get_pending_auth_requests(self, user_id: int) -> List[AuthRequest]:
        """Get pending authentication requests for a user."""
        return self.repository.get_pending_auth_requests(user_id)

    def complete_auth_request(self, auth_request_id: int, target_user_id: int, 
                            credentials: str, platform_config: str = None) -> int:
        """Complete authentication request by creating recipient."""
        try:
            # Validate auth request
            auth_request = self.repository.get_auth_request_by_id(auth_request_id)
            if not auth_request:
                raise ValueError("Authentication request not found")
            
            if auth_request.target_user_id != target_user_id:
                raise ValueError("Not authorized to complete this request")
            
            if not auth_request.is_active():
                raise ValueError("Authentication request expired or not active")
            
            # Create recipient for requester
            from models.unified_recipient import UnifiedRecipientCreate
            
            recipient = UnifiedRecipientCreate(
                name=auth_request.recipient_name,
                platform_type=auth_request.platform_type,
                credentials=credentials,
                platform_config=platform_config,
                is_personal=True,
                is_default=False,  # Let the repository decide
                enabled=True
            )
            
            recipient_id = self.repository.add_recipient(
                auth_request.requester_user_id,
                recipient
            )
            
            # Update auth request status
            self.repository.update_auth_request_status(
                auth_request_id, 'completed', recipient_id
            )
            
            logger.info(f"Completed auth request {auth_request_id}, created recipient {recipient_id}")
            return recipient_id
            
        except Exception as e:
            logger.error(f"Error completing auth request: {e}")
            raise

    def cancel_auth_request(self, auth_request_id: int, user_id: int) -> bool:
        """Cancel authentication request."""
        try:
            auth_request = self.repository.get_auth_request_by_id(auth_request_id)
            if not auth_request:
                return False
            
            # Only requester or target can cancel
            if user_id not in [auth_request.requester_user_id, auth_request.target_user_id]:
                raise ValueError("Not authorized to cancel this request")
            
            return self.repository.update_auth_request_status(auth_request_id, 'cancelled')
            
        except Exception as e:
            logger.error(f"Error cancelling auth request: {e}")
            return False

    def cleanup_expired_requests(self):
        """Clean up expired authentication requests."""
        try:
            count = self.repository.cleanup_expired_auth_requests()
            if count > 0:
                logger.info(f"Cleaned up {count} expired authentication requests")
            return count
        except Exception as e:
            logger.error(f"Error cleaning up expired requests: {e}")
            return 0