# TGHandyUtils 🧛‍♂️🚀

Are you tired of manually creating tasks in Todoist after every chat with your friends, boss, or worse... your mom? Fear not, because **TGHandyUtils** is here to save your day! With our completely over-engineered AI-powered bot, you can finally live the hands-free life you've always dreamed of. 🚀

## ✨ Features

- 🎯 **Smart Task Creation**: Converts your messages into structured tasks automatically
- 🎤 **Voice Message Support**: Transcribes voice messages using OpenAI Whisper  
- 🌍 **Timezone Intelligence**: Properly handles local time zones (Portugal, UK, US, etc.)
- 📋 **Multi-Platform Support**: Works with Todoist and Trello seamlessly
- 🔄 **Interactive UI**: Beautiful inline keyboards and task management interface
- 👥 **Multi-User**: Supports unlimited users with individual configurations (data properly isolated, no chaos!)
- ⏰ **Smart Scheduling**: Understands natural language like "tomorrow at 3 PM"
- 🏥 **Real-World Ready**: Handles appointment reminders, meeting scheduling, etc.

## 🚀 How it Works

### **Text Messages** 
1. Send any message: "Remind me to call doctor tomorrow at 3 PM"
2. Bot parses your message using AI and creates a task 
3. Displays confirmation with **local time**: "Due: June 12, 2025 at 15:00 (Portugal time)"

### **Voice Messages**
1. Send a voice message in any language
2. Bot transcribes using OpenAI Whisper 
3. Asks for confirmation of transcription
4. Creates task with proper scheduling

### **Smart Features**
- **Timezone Conversion**: Automatically converts your local time to UTC for storage
- **Natural Language**: Understands "tomorrow", "next Monday", "in 2 hours"
- **Date Formats**: Handles "12/Jun", "June 12", "Dec 15" formats  
- **Interactive Menus**: Browse tasks, quick actions, settings via inline keyboards

### **Calendar Integration** 
Connect your tasks to calendars:
- **Todoist**: [Google Calendar Integration](https://todoist.com/help/articles/use-the-calendar-integration-rCqwLCt3G)
- **Trello**: [Google Calendar Power-Up](https://trello.com/power-ups/55a5d917446f517774210013/google-calendar)

## Setup

Okay, brave soul. Ready to set this thing up? Follow these not-so-simple steps:

### Step 1: Clone the Repository
```bash
git clone https://github.com/artmoskal/TGHandyUtils.git
cd TGHandyUtils
```

### Step 2: Create Your `.env` File
You're going to need some secrets. Create a `.env` file at the root and paste in your super confidential API keys:

```
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
OPENAI_API_KEY=your_openai_api_key_here

```

You didn't forget to get those keys, did you? Here's where to find them:
- [Telegram Bot Token](https://core.telegram.org/bots/tutorial)
- [OpenAI API Key](https://help.openai.com/en/articles/4936850-where-do-i-find-my-openai-api-key) 

During your first interaction with the bot, it will ask you to choose your preferred task management platform:

- **Todoist**: Provide your API token from Settings → Integrations → Developer tab
- **Trello**: Provide your API key and token in format `API_KEY:TOKEN` from [Power-Up Admin Portal](https://trello.com/power-ups/admin)

**✅ Updated Setup Process (2025):**
1. **Platform Selection**: Choose Todoist or Trello
2. **API Configuration**: Enter your credentials securely  
3. **Location Setup**: Set your timezone (e.g., "Portugal", "Cascais", "New York")
4. **Board/List Selection** (Trello only): Choose which board and list for tasks

Your chosen platform and credentials will be securely stored in the database for future use. No need to re-enter your tokens every time you sneeze or switch platforms - we've got your back! 🔐

### Step 3: Deploy with Docker
It's 2024, and we all know Docker is life. Let's containerize this bad boy:

1. Build and run with Docker Compose:
   ```bash
   docker-compose -f infra/docker-compose.yml up -d
   ```

That's it! Now your bot is running in an isolated container, just as nature intended.

### Step 4: Update User Settings

During the first interaction, the bot will ask you to select your preferred task management platform and provide the necessary credentials. You can also change your platform at any time by using the `/set_platform` command.

### Step 5: Install Dependencies with Conda and Run Locally (For Local Testing)
If you're testing locally and Docker isn't your thing, you can still run it the old-fashioned way:

1. Install dependencies:
   ```bash
   conda env create -f environment.yaml
   conda activate todoist-taskbot-env
   ```

2. Run the bot:
   ```bash
   python3 main.py
   ```

SQLite will create the database file automatically if it doesn't exist. Hopefully, the bot starts up without too much judgment from Python itself.

### Step 5: Marvel at the Architecture 🏗️
This thing has more interfaces than a spaceship and enough dependency injection to make Spring Boot jealous. But hey, at least it works and won't explode when multiple users try to create tasks simultaneously!

### Step 6: Pray 🙏
Because even with professional architecture, software never works the first time.

## 📱 Commands & Features

### **Bot Commands**
- `/start` - Welcome message and onboarding
- `/menu` - Main interactive menu  
- `/tasks` - View and manage your pending tasks
- `/settings` - View platform and timezone settings
- `/set_platform` - Change or reconfigure your task platform
- `/drop_user_data` - Delete all your data (with confirmation)

### **Interactive Features** 
- **Main Menu**: Task overview, quick tasks, settings, help
- **Task Management**: View, complete, edit, reschedule, delete tasks  
- **Quick Tasks**: Pre-configured tasks (coffee break, shopping, medicine)
- **Voice Transcription**: Confirm/cancel voice message parsing
- **Platform Setup**: Step-by-step platform configuration

### **Smart Parsing Examples**
```
"Doctor appointment 12/Jun, 12:00" 
→ Creates task for June 12, 2025 at 12:00 (your local time)

"Buy groceries tomorrow at 5 PM"
→ Creates task for next day at 17:00 (your local time)

"Call mom in 2 hours" 
→ Creates task 2 hours from now
```

## Contributing

Found a bug? Cool. You want to fix it? Even better. Make a pull request and let's talk about it like adults (or just blame it on Python).

Contact 4spamartem@gmail.com (yes, it's real email).
## License

We believe in open-source software, and we're not really in this for the money anyway. MIT License.

---

## ✅ **Latest Updates (June 2025)**

### **🔧 Bug Fixes Applied**
- **Timezone Conversion**: Fixed Portugal/Cascais time handling (UTC+1)
- **Date Parsing**: Fixed "12/Jun" now correctly becomes "June 12, 2025" 
- **Local Time Display**: All times now show in your local timezone, not UTC
- **Todoist Setup**: Corrected API token instructions (Settings → Integrations → Developer)

### **🎯 Enhanced Features**  
- **Interactive UI**: Complete main menu system with inline keyboards
- **Task Management**: View, edit, complete, delete tasks with pagination
- **Voice Processing**: Improved transcription workflow with confirmation
- **Multi-Platform**: Full Trello integration with board/list selection
- **Smart Scheduling**: Better natural language understanding

### **🌍 Supported Timezones**
Portugal, Spain, France, Germany, UK, New York, California, and more!

---

### **⚠️ Disclaimer** 
This bot handles real appointments and reminders. While extensively tested, always double-check important scheduling. Voice transcription accuracy depends on audio quality and OpenAI Whisper performance.

**Architecture**: Built with modern Python, Docker, SQLite, and ~~probably too much~~ proper dependency injection. Production-ready with error handling, logging, user data isolation, and enough abstraction layers to make a senior architect weep with joy (or terror). Features thread-safe multi-user support, encrypted token storage, and a test suite that actually passes! 🎯

## 🧪 **Testing**

Comprehensive test suite with unit and integration tests running in Docker:

### **Quick Testing**
```bash
# Run all unit tests (recommended for development)
./test-dev.sh unit

# Run all integration tests  
./test-dev.sh integration

# Run all tests with full coverage report
./test.sh
```

### **Test Categories**
- **Unit Tests**: Services, platforms, database repositories, models
- **Integration Tests**: End-to-end workflows, multi-component interactions
- **Coverage**: Targeting 80%+ code coverage across core components

### **What's Tested**
- ✅ **Parsing Service**: Timezone conversion, LLM integration, error handling
- ✅ **Task Service**: CRUD operations, platform integration, user management  
- ✅ **Platform APIs**: Todoist/Trello integration, authentication, error handling
- ✅ **Database Layer**: Repository pattern, data persistence, transactions
- ✅ **Workflows**: Complete task creation from message to platform

**Note**: Tests run in isolated Docker containers matching production environment.

## 🔒 **Multi-User & Security**

### **User Data Isolation**
- Each Telegram user gets their own isolated data space
- Your tasks won't accidentally show up in someone else's feed
- Platform tokens are stored per user (no sharing of API keys)
- Thread-safe message processing prevents race conditions

### **Key Persistence** 
- Enter your Todoist/Trello credentials once, use forever
- Switch between platforms without re-authentication  
- Secure database storage with proper encryption
- No more "wait, what was my API token again?" moments

### **Security Features**
- All sensitive data properly isolated by user ID
- Database foreign key constraints prevent data leakage
- Thread-safe operations for concurrent users
- WAL mode SQLite for better concurrency performance

Basically, you can share this bot with your entire team/family and nobody will mess up each other's tasks. Revolutionary stuff! 🚀

Enjoy responsibly! 🍻

