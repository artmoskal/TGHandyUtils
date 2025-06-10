"""Enhanced onboarding service for better user experience."""

from typing import Dict, Any, Optional
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from core.interfaces import ITaskService
from core.logging import get_logger

logger = get_logger(__name__)

class OnboardingService:
    """Service for managing user onboarding experience."""
    
    def __init__(self, task_service: ITaskService):
        self.task_service = task_service
    
    async def send_welcome_message(self, message: Message) -> None:
        """Send a comprehensive welcome message to new users."""
        welcome_text = """
🎉 **Welcome to TGHandyUtils!**

I'm your personal task management assistant. I can help you:

📝 **Create tasks** from text or voice messages
⏰ **Schedule reminders** with natural language
🔗 **Sync with platforms** like Todoist and Trello
🗣️ **Process voice messages** into organized tasks

To get started, you'll need to connect your task management platform.

**Supported Platforms:**
• **Todoist** - Simple API token setup
• **Trello** - Board and list selection

Ready to begin? Use /set_platform to get started! 🚀
        """
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🛠️ Set Up Platform", callback_data="start_setup")]
        ])
        
        await message.answer(welcome_text, reply_markup=keyboard, parse_mode='Markdown')
    
    async def get_onboarding_status(self, user_id: int) -> Dict[str, Any]:
        """Get the current onboarding status for a user."""
        user_info = self.task_service.get_user_platform_info(user_id)
        
        status = {
            'is_complete': False,
            'has_platform': False,
            'has_location': False,
            'platform_type': None,
            'missing_steps': []
        }
        
        if user_info and user_info.get('platform_token'):
            status['has_platform'] = True
            status['platform_type'] = user_info.get('platform_type')
            
            if user_info.get('location'):
                status['has_location'] = True
                status['is_complete'] = True
            else:
                status['missing_steps'].append('location')
        else:
            status['missing_steps'].extend(['platform', 'location'])
        
        return status
    
    async def send_progress_update(self, message: Message, user_id: int) -> None:
        """Send onboarding progress update to user."""
        status = await self.get_onboarding_status(user_id)
        
        if status['is_complete']:
            text = """
✅ **Setup Complete!**

Your account is fully configured:
• Platform: {platform}
• Location: Set ✓

You can now start creating tasks! Just send me a message or voice note.

**Quick Commands:**
• `/settings` - View your settings
• `/set_platform` - Change platform
• Send any message to create a task

Try saying: "Remind me to buy groceries tomorrow at 3 PM"
            """.format(platform=status['platform_type'].title())
            
        elif status['has_platform']:
            text = """
🔄 **Almost Done!**

Platform setup: ✅ {platform}
Location setup: ❌ Pending

Please provide your location (city or country) for accurate time zone handling.
            """.format(platform=status['platform_type'].title())
            
        else:
            text = """
🚀 **Let's Get Started!**

To create and manage tasks, I need to connect to your task management platform.

Choose your platform below:
            """
        
        if not status['is_complete']:
            keyboard = self._get_progress_keyboard(status)
            await message.answer(text, reply_markup=keyboard, parse_mode='Markdown')
        else:
            await message.answer(text, parse_mode='Markdown')
    
    def _get_progress_keyboard(self, status: Dict[str, Any]) -> InlineKeyboardMarkup:
        """Generate keyboard based on onboarding progress."""
        buttons = []
        
        if not status['has_platform']:
            buttons.extend([
                [InlineKeyboardButton(text="📱 Todoist", callback_data="platform_todoist")],
                [InlineKeyboardButton(text="📋 Trello", callback_data="platform_trello")]
            ])
        
        # Always add settings option if partially configured
        if status['has_platform'] or status['has_location']:
            buttons.append([InlineKeyboardButton(text="⚙️ View Settings", callback_data="show_settings")])
        
        return InlineKeyboardMarkup(inline_keyboard=buttons)
    
    def get_platform_help_text(self, platform_type: str) -> str:
        """Get platform-specific help text."""
        if platform_type == "todoist":
            return """
📱 **Todoist Setup**

To connect your Todoist account:

1. Go to https://todoist.com/app/settings/integrations
2. Click the **"Developer"** tab
3. Copy your API token
4. Send it to me here

🔒 Your token is stored securely and only used to create tasks.
            """
        
        elif platform_type == "trello":
            return """
📋 **Trello Setup**

To connect your Trello account:

1. **Go to:** https://trello.com/power-ups/admin
2. **Create a Power-Up** (or use existing one)
3. **Go to "API Key" tab** in your Power-Up settings
4. **Copy your API Key**
5. **Generate a Token** (click "Generate a new API Token")
6. **Authorize the token** for your account
7. **Send me:** `YOUR_API_KEY:YOUR_TOKEN`

**Example:** `abc123def456:xyz789abc123def456`

**Alternative (legacy):** Try https://trello.com/app-key if above doesn't work

🔒 Your credentials are stored securely and only used for task creation.
            """
        
        return "Platform setup instructions not available."
    
    def get_location_help_text(self) -> str:
        """Get location setup help text."""
        return """
🌍 **Location Setup**

Please provide your location for accurate time zone handling.

You can send:
• City name: "New York", "London", "Tokyo"
• Country: "USA", "UK", "Japan"  
• Time zone: "UTC+3", "EST"

This helps me schedule your tasks at the right time! ⏰
        """

# Remove global instance - use DI container instead