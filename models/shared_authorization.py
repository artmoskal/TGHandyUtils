from dataclasses import dataclass
from datetime import datetime
from typing import Optional

@dataclass
class SharedAuthorization:
    id: int
    owner_user_id: int
    grantee_user_id: int
    owner_recipient_id: int
    permission_level: str = 'use'  # 'use', 'admin'
    status: str = 'pending'  # 'pending', 'accepted', 'revoked', 'declined'
    created_at: datetime = None
    updated_at: datetime = None
    last_used_at: Optional[datetime] = None
    
    def is_active(self) -> bool:
        return self.status == 'accepted'
    
    def can_use(self) -> bool:
        return self.status == 'accepted' and self.permission_level in ['use', 'admin']
    
    def can_admin(self) -> bool:
        return self.status == 'accepted' and self.permission_level == 'admin'