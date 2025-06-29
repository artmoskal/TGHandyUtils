# TGHandyUtils 🧛‍♂️🚀

Are you tired of manually creating tasks in Todoist after every chat with your friends, boss, or worse... your mom? Fear not, because **TGHandyUtils** is here to save your day! With our completely over-engineered AI-powered bot, you can finally live the hands-free life you've always dreamed of. 🚀

## ✨ Features

- 🎯 **Smart Task Creation**: Converts your messages into structured tasks automatically
- 🎤 **Voice Message Support**: Transcribes voice messages using OpenAI Whisper  
- 📸 **Image Processing**: Supports both inline photos and document image attachments with OCR
- 🔔 **Notification Control**: Toggle Telegram notifications while keeping platform task creation
- 🌍 **Timezone Intelligence**: Properly handles local time zones (Portugal, UK, US, etc.)
- 📋 **Multi-Platform Support**: Works with Todoist and Trello seamlessly (extensible architecture)
- 🔄 **Interactive UI**: Beautiful inline keyboards and task management interface
- 👥 **Multi-User**: Supports unlimited users with individual configurations (data properly isolated, no chaos!)
- ⏰ **Smart Scheduling**: Understands natural language like "tomorrow at 3 PM"
- 🏥 **Real-World Ready**: Handles appointment reminders, meeting scheduling, etc.
- 🤝 **Recipient System**: Share tasks with family/colleagues across different platforms (e.g., your Todoist + wife's Trello)

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

### **Image Messages (NEW!)**
1. Send any image (inline photo or document attachment) with caption
2. Bot extracts text using OCR and analyzes image content
3. Creates task combining caption text, extracted text, and image context
4. Supports both compressed photos and uncompressed document attachments

### **Recipient System (NEW!)**
1. Use `/recipients` to manage your platforms and shared recipients
2. Add your own platforms (My Todoist, My Trello)
3. Add shared recipients with others' credentials (Wife's Trello, Team Todoist)
4. Enable recipient selection UI to choose where tasks go
5. Create tasks that appear on multiple platforms simultaneously

### **Smart Features**
- **Timezone Conversion**: Automatically converts your local time to UTC for storage
- **Natural Language**: Understands "tomorrow", "next Monday", "in 2 hours"
- **Date Formats**: Handles "12/Jun", "June 12", "Dec 15" formats  
- **Interactive Menus**: Browse tasks, quick actions, settings via inline keyboards
- **Recipient Management**: Add/edit/disable recipients without affecting existing tasks

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

### Step 5: Enjoy! 🎉
The bot is now running and ready to handle your tasks. Send it a message and watch the magic happen!

### Step 6: Share with Friends 🤝
Because everyone deserves to escape the task management nightmare.

## 📱 Commands & Features

### **Bot Commands**
- `/start` - Welcome message and quick start guide
- `/recipients` - Manage your platforms and shared recipients
- `/create_task` - Create a new task with recipient selection

### **Interactive Features** 
- **Platform Management**: Add/edit/disable your platforms and shared recipients
- **Recipient Settings**: Toggle UI selection, enable/disable Telegram notifications
- **Task Creation**: Choose recipients or use default platforms
- **Voice Transcription**: Confirm/cancel voice message parsing
- **Smart Setup**: Direct links to get API tokens with step-by-step guides
- **Visual Status**: Platform emojis (📝 Todoist, 📋 Trello) and active/disabled indicators

### **Smart Parsing Examples**
```
"Doctor appointment 12/Jun, 12:00" 
→ Creates task for June 12, 2025 at 12:00 (your local time)

"Buy groceries tomorrow at 5 PM"
→ Creates task for next day at 17:00 (your local time)

"Call mom in 2 hours" 
→ Creates task 2 hours from now

[Image with caption: "Fix this bug in 3h"]
→ Extracts text from image + caption, creates task due in 3 hours
```

## Contributing

Found a bug? Cool. You want to fix it? Even better. Make a pull request and let's talk about it like adults (or just blame it on Python).

Contact 4spamartem@gmail.com (yes, it's real email).
## License

We believe in open-source software, and we're not really in this for the money anyway. MIT License.

---

## ✅ **Latest Updates (June 2025)**

### **🚀 Major New Features**
- **📸 Universal Image Support**: Now handles both inline photos AND document image attachments with OCR
- **🔔 Notification Control**: Toggle Telegram notifications while keeping platform task creation
- **🔧 Plugin Architecture**: Extensible platform system with registry pattern for easy new platform additions
- **⚙️ Multi-Platform UI**: Enhanced settings interface for managing multiple platforms

### **🔧 Bug Fixes Applied**
- **Timezone Conversion**: Fixed Portugal/Cascais time handling (UTC+1)
- **Date Parsing**: Fixed "12/Jun" now correctly becomes "June 12, 2025" 
- **Local Time Display**: All times now show in your local timezone, not UTC
- **State Management**: Fixed API key/task flow contamination issues
- **Error Handling**: Improved validation and prevented empty message processing

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

**Architecture**: Built with modern Python, Docker, and ~~probably too much~~ proper engineering. Production-ready with thread-safe multi-user support and a test suite that actually passes! 🎯

## 🧪 **Testing & Quality**

Comprehensive test suite ensures reliability:

```bash
# Run all tests
./test.sh
```

✅ **Thoroughly tested** with unit and integration tests  
✅ **Production-ready** architecture with proper error handling  
✅ **Multi-user safe** with isolated data and thread-safe operations

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

