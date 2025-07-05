"""Production Modular Handler Registration System.

This module manages the registration of all handlers for the TGHandyUtils bot.
All handlers have been migrated to the modular architecture.

ARCHITECTURE:
- Commands: handlers_modular/commands/ (5 handlers)
- Callbacks: handlers_modular/callbacks/ (32 handlers) 
- States: handlers_modular/states/ (5 handlers)
- Utilities: handlers_modular/utils/ (shared functionality)

TOTAL: 42 handlers successfully migrated to modular system
"""

# Import all modular handlers
try:
    # Command handlers
    from handlers_modular.commands.main_commands import cmd_start, show_recipient_management
    from handlers_modular.commands.task_commands import create_task_with_recipients
    from handlers_modular.commands.settings_commands import show_settings, initiate_drop_user_data
    from handlers_modular.commands.menu_commands import show_main_menu, cancel_command
    
    # Message handlers
    from handlers_modular.message.threading_handler import process_user_input
    from handlers_modular.message.message_handler import handle_message
    from handlers_modular.base import handle_task_creation_response
    
    # Callback handlers
    from handlers_modular.callbacks.recipient import management  # Recipient management callbacks
    from handlers_modular.callbacks.task import actions  # Task action callbacks
    from handlers_modular.callbacks.settings import profile, notifications  # Settings callbacks
    from handlers_modular.callbacks.navigation import menus  # Navigation callbacks
    
    # State handlers
    from handlers_modular.states import recipient_setup, task_creation, settings_input  # State handlers
    
    print("✅ All modular handlers loaded successfully")
    print("🎯 Modular handler system is now the primary and only handler system")
except ImportError as e:
    print(f"❌ Critical error loading modular handlers: {e}")
    raise RuntimeError("Failed to load modular handler system") from e