"""Unified handler registration - Phase 2 transition approach.

This module serves as a bridge during the transition from monolithic handlers.py
to the new modular handlers/ structure. It imports handlers from both sources
and can be gradually migrated over time.
"""

# Import all modular handlers (newly created)
try:
    from handlers_modular.commands.main_commands import cmd_start, show_recipient_management
    from handlers_modular.message.threading_handler import process_user_input
    from handlers_modular.base import handle_task_creation_response
    print("✅ Modular handlers loaded successfully")
except ImportError as e:
    print(f"⚠️  Modular handlers import error: {e}")

# Import remaining handlers from monolithic file
# These will be gradually migrated to the modular structure
try:
    # Import all remaining handlers from the monolithic file  
    # We'll keep the monolithic file as fallback during transition
    import handlers  # This imports the full monolithic handlers.py
    print("✅ Monolithic handlers loaded as fallback")
except ImportError as e:
    print(f"❌ Failed to load handlers: {e}")
    raise

print("🔄 Phase 2: Handler system loaded (hybrid monolithic + modular)")