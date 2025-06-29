# Unified Recipients UI/UX Menu Flow

## Main Menu Structure

```
🎯 Main Menu (/menu)
├── 📝 Create Task
│   └── [Task Creation Flow]
│
├── 📱 My Accounts (show_recipients)
│   ├── 📱 Your Connected Accounts:
│   │   ├── ✅👤📝 My Todoist (personal)
│   │   ├── ✅👥📋 Alona (shared Trello)
│   │   └── [Other accounts...]
│   │
│   ├── ➕ Add Personal Account
│   │   ├── 📝 Todoist
│   │   └── 📋 Trello
│   │       └── [Board & List Selection]
│   │
│   ├── 👥 Add Shared Account
│   │   ├── 📝 Todoist
│   │   └── 📋 Trello
│   └── « Back to Menu
│
└── ⚙️ Settings (show_settings)
    ├── 👤 Profile Settings
    │   ├── 👤 Update Name
    │   ├── 🌍 Update Location
    │   └── « Back to Settings
    │
    ├── 📱 Manage Accounts (show_recipients) ← NEW!
    │   └── [Goes to My Accounts above]
    │
    ├── 🔔 Notifications
    │   ├── 🔄 Toggle Telegram Notifications
    │   ├── 🔄 Toggle Recipient UI
    │   └── « Back to Settings
    │
    ├── 🗑️ Delete All Data
    │   ├── 🗑️ DELETE ALL DATA
    │   └── ❌ Cancel
    │
    └── « Back to Menu
```

## Account Management Flow

```
📱 Account Details (recipient_edit_{id})
├── 🔄 Enable/Disable Account
├── ⚙️ Configure Trello Board (Trello only)
├── 🗑️ Delete Account
└── « Back to Accounts
```

## Task Creation Flow

```
📝 Task Creation
├── [Text/Voice/Photo Input Processing]
├── 🎯 Select Recipients:
│   ├── ☑️ Personal accounts (auto-selected)
│   ├── ☐ Shared accounts (manual selection)
│   ├── ✅ Create Task
│   └── ❌ Cancel
│
└── [Post-Task Actions]
    ├── ❌ Remove from [Account]
    ├── ➕ Add to [Other Account]
    └── ✅ Done
```

## Key Navigation Principles

### 1. **Unified Access**
- All accounts (personal & shared) accessible from single "My Accounts" menu
- No separate "Recipients" vs "Platforms" confusion
- Clear visual distinction: 👤 personal, 👥 shared

### 2. **Settings Integration**
- "📱 Manage Accounts" button in Settings provides direct access
- No navigation dead-ends
- Consistent back button behavior

### 3. **Clear Status Indicators**
- ✅ Active accounts
- ⚠️ Disabled accounts
- Platform icons: 📝 Todoist, 📋 Trello

### 4. **Logical Grouping**
- Profile settings separate from account management
- Notifications centralized
- Destructive actions (delete) clearly marked

## Menu Labels & Consistency

| Menu Level | Label Format | Example |
|------------|-------------|---------|
| Main Menu | 🔰 Action | 📝 Create Task |
| Settings | 👤 Category | 👤 Profile Settings |
| Accounts | ✅👤📝 Status+Type+Platform+Name | ✅👤📝 My Todoist |
| Actions | ➕/❌/🔄 Action | ➕ Add Personal Account |
| Navigation | « Back to [Context] | « Back to Settings |

## Error Prevention

### 1. **No Orphaned Menus**
- Every menu has clear path back to parent
- Settings → Accounts → Back to Accounts → Back to Menu

### 2. **Contextual Actions**
- Only show relevant options (Configure Trello for Trello accounts)
- Disable/hide non-applicable actions

### 3. **Confirmation for Destructive Actions**
- Delete confirmation screens
- Clear cancel options

## Implementation Status

✅ **Completed:**
- Unified recipients table architecture
- Clean service layer without ID prefixing
- Fixed task creation with real API calls
- Added "📱 Manage Accounts" to Settings menu
- Proper navigation handlers

✅ **Menu Flow Verified:**
- Main Menu → Settings → Manage Accounts → Account Details
- Main Menu → My Accounts → Add Account → Platform Selection
- Settings → Profile Settings → Update fields
- All back buttons working correctly

This UI/UX structure ensures users can easily navigate between all functionality without getting lost or encountering dead ends. The unified recipients architecture supports this clean interface by treating all accounts as equal entities with clear type distinctions.