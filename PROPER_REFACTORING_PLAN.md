# Proper Architecture Refactoring Plan

## Current Problems

### 1. **Terminology is Wrong**
- "Partner" implies external people, but user is also a "partner"
- "Partner Management" is confusing when managing your own platforms
- Mixed concepts: platforms vs partners vs users

### 2. **Architecture is Conceptually Fucked**
- Three different storage systems for the same thing
- Dual code paths everywhere
- "Self" partner concept is weird
- No clear separation between users and their platforms

### 3. **Dirty Patches Instead of Clean Design**
- Checking both legacy and new systems everywhere
- Try/catch blocks to handle DI failures
- Multiple ways to access the same data
- Inconsistent behavior

## Proper Architecture Design

### Core Concepts

#### 1. **Recipients** (not "partners")
- **Recipients** = destinations where tasks can be created
- User's own platforms = recipients owned by the user
- Other people's platforms = recipients shared with the user
- Clean terminology: "Create task for these recipients"

#### 2. **User Platforms** 
- Each user can have multiple platforms (Todoist, Trello, etc.)
- User owns and manages their platforms
- Platforms are independent - user can have both Todoist AND Trello

#### 3. **Shared Recipients**
- Other people can share their platforms as recipients
- "Wife's Trello", "Team Todoist", etc.
- Recipient has: name, platform_type, credentials, owner info

### Database Schema (Clean)

```sql
-- Users table (minimal, just user info)
CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    telegram_user_id INTEGER UNIQUE NOT NULL,
    name TEXT NOT NULL,
    location TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- User platforms (user's own platforms)
CREATE TABLE user_platforms (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    platform_type TEXT NOT NULL, -- 'todoist', 'trello'
    credentials TEXT NOT NULL,
    platform_config TEXT, -- JSON config
    enabled BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, platform_type)
);

-- Shared recipients (platforms shared by others)
CREATE TABLE shared_recipients (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id), -- who has access
    name TEXT NOT NULL, -- "Wife's Trello", "Team Todoist"
    platform_type TEXT NOT NULL,
    credentials TEXT NOT NULL,
    platform_config TEXT,
    shared_by TEXT, -- who shared it
    enabled BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- User preferences
CREATE TABLE user_preferences (
    user_id INTEGER PRIMARY KEY REFERENCES users(id),
    default_recipients TEXT, -- JSON array of recipient IDs
    show_recipient_ui BOOLEAN DEFAULT FALSE,
    telegram_notifications BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Task recipients (which recipients got each task)
CREATE TABLE task_recipients (
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    recipient_type TEXT NOT NULL, -- 'user_platform' or 'shared_recipient'
    recipient_id INTEGER NOT NULL, -- ID in respective table
    platform_task_id TEXT, -- external platform task ID
    status TEXT DEFAULT 'created', -- 'created', 'failed'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (task_id, recipient_type, recipient_id)
);
```

### Service Architecture (Clean)

```python
# Single service to handle all recipients
class RecipientService:
    def get_all_recipients(self, user_id: int) -> List[Recipient]
    def get_user_platforms(self, user_id: int) -> List[UserPlatform]  
    def get_shared_recipients(self, user_id: int) -> List[SharedRecipient]
    def add_user_platform(self, user_id: int, platform: PlatformCreate) -> str
    def add_shared_recipient(self, user_id: int, recipient: RecipientCreate) -> str
    def remove_recipient(self, user_id: int, recipient_id: str, recipient_type: str) -> bool

# Task service only deals with recipients
class TaskService:
    def create_task(self, user_id: int, task_data: TaskCreate, recipients: List[str]) -> bool
    # recipients = list of recipient IDs (both user platforms and shared)
```

### UI/UX (Clean)

```
Settings:
├── My Platforms (manage your own Todoist, Trello, etc.)
├── Shared Recipients (platforms shared by others)  
├── Task Preferences (defaults, notifications)
└── Recipient Selection (enable/disable selection UI)

Task Creation:
├── Quick Create (uses default recipients)
└── Choose Recipients (select from all available)
    ├── My Platforms: [✓ My Todoist] [✓ My Trello]
    └── Shared: [✓ Wife's Trello] [ ] Team Todoist
```

## Migration Strategy

### Phase 1: Database Schema Migration
1. Create new clean tables
2. Migrate data from old tables:
   - users.platform_token + platform_type → user_platforms
   - user_partners where is_self=true → user_platforms  
   - user_partners where is_self=false → shared_recipients
   - user_preferences → clean user_preferences
3. Keep old tables during transition

### Phase 2: Service Layer Refactor
1. Create new RecipientService with clean interface
2. Update TaskService to use recipients instead of partners
3. Remove all dual-system code paths
4. Single way to access recipient data

### Phase 3: UI/UX Update  
1. Replace "Partner Management" with "Recipients"
2. Separate "My Platforms" from "Shared Recipients"
3. Update all terminology throughout UI
4. Clear recipient selection interface

### Phase 4: Remove Legacy Code
1. Drop old tables
2. Remove legacy service methods
3. Clean up all try/catch patches
4. Remove duplicate code paths

## Implementation Plan

### Step 1: Models & Schema
```python
@dataclass
class UserPlatform:
    id: str
    platform_type: str  # 'todoist', 'trello'
    credentials: str
    config: Optional[Dict[str, Any]]
    enabled: bool

@dataclass  
class SharedRecipient:
    id: str
    name: str  # "Wife's Trello"
    platform_type: str
    credentials: str
    config: Optional[Dict[str, Any]]
    shared_by: str
    enabled: bool

@dataclass
class Recipient:
    id: str
    name: str  # "My Todoist" or "Wife's Trello"
    platform_type: str
    type: str  # 'user_platform' or 'shared_recipient'
    enabled: bool
```

### Step 2: Repository Layer
```python
class UserPlatformRepository:
    def get_user_platforms(self, user_id: int) -> List[UserPlatform]
    def add_platform(self, user_id: int, platform: UserPlatformCreate) -> str
    def remove_platform(self, user_id: int, platform_type: str) -> bool

class SharedRecipientRepository:
    def get_shared_recipients(self, user_id: int) -> List[SharedRecipient]
    def add_recipient(self, user_id: int, recipient: SharedRecipientCreate) -> str
    def remove_recipient(self, user_id: int, recipient_id: str) -> bool
```

### Step 3: Service Layer
```python
class RecipientService:
    def __init__(self, platform_repo: UserPlatformRepository, shared_repo: SharedRecipientRepository):
        self.platform_repo = platform_repo
        self.shared_repo = shared_repo
    
    def get_all_recipients(self, user_id: int) -> List[Recipient]:
        # Combine user platforms + shared recipients into unified list
        platforms = self.platform_repo.get_user_platforms(user_id)
        shared = self.shared_repo.get_shared_recipients(user_id)
        return self._combine_to_recipients(platforms, shared)
```

### Step 4: Task Creation
```python
class TaskService:
    def create_task(self, user_id: int, task_data: TaskCreate, recipient_ids: List[str]) -> bool:
        recipients = self.recipient_service.get_recipients_by_ids(user_id, recipient_ids)
        
        for recipient in recipients:
            # Create task on each recipient's platform
            self._create_task_on_platform(task_data, recipient)
```

## Benefits of Clean Architecture

### 1. **Clear Concepts**
- User platforms = platforms you own
- Shared recipients = platforms others shared with you  
- Recipients = all destinations where tasks can go
- No confusion about "self partners"

### 2. **Single Code Path**
- One way to store platform data
- One way to access recipients
- No dual system checks
- No try/catch patches

### 3. **Scalable Design**
- Easy to add new platform types
- Easy to add new sharing models
- Clear separation of concerns
- Proper foreign key relationships

### 4. **Better UX**
- Clear terminology throughout
- Logical organization of settings
- Intuitive recipient selection
- No confusion about partners vs platforms

## Migration Compatibility

### During Migration:
- Keep old tables for data safety
- Gradual migration of users
- Backward compatibility for existing tasks
- Clear migration path

### After Migration:
- Clean codebase with single patterns
- Proper foreign key constraints
- No legacy code paths
- Consistent terminology

This is the **proper refactor** that should have been done from the start, not the dirty patches we've been applying.