from dataclasses import dataclass
from datetime import datetime
from typing import Optional

@dataclass
class AuthRequest:
    id: int
    requester_user_id: int
    target_user_id: int
    platform_type: str
    recipient_name: str
    status: str = 'pending'  # 'pending', 'completed', 'expired', 'cancelled'
    expires_at: datetime = None
    completed_recipient_id: Optional[int] = None
    created_at: datetime = None
    updated_at: datetime = None
    
    def is_active(self) -> bool:
        return self.status == 'pending' and self.expires_at > datetime.utcnow()
    
    def is_completed(self) -> bool:
        return self.status == 'completed'