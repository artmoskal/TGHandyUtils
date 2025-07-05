"""Text parsing service using LangChain and OpenAI."""

from typing import Optional, Dict, Any
from datetime import datetime, timezone, timedelta
import zoneinfo

from langchain_openai import ChatOpenAI
from langchain.prompts import PromptTemplate
from langchain.output_parsers import PydanticOutputParser
from langchain_core.messages import HumanMessage

from models.task import TaskCreate
from core.interfaces import IParsingService, IConfig
from core.exceptions import ParsingError
from core.logging import get_logger

logger = get_logger(__name__)

class ParsingService(IParsingService):
    """Service for parsing text into structured task data."""
    
    def __init__(self, config: IConfig):
        self.config = config
        
        if not config.OPENAI_API_KEY:
            raise ValueError("OpenAI API key is required for parsing service")
        
        self.llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.0,  # Maximum precision for mathematical calculations
            openai_api_key=config.OPENAI_API_KEY
        )
        
        self.parser = PydanticOutputParser(pydantic_object=TaskCreate)
        self.prompt_template = self._create_prompt_template()
    
    def _create_prompt_template(self) -> PromptTemplate:
        """Create the prompt template for task parsing."""
        template = """
        You are a multilingual task creation assistant. Create a task with 'title', 'due_time' (UTC ISO 8601), and 'description'.

        CURRENT CONTEXT:
        - Current UTC: {current_utc_iso}
        - Current Local: {current_local_iso} ({location})
        - Timezone: {timezone_name} (UTC{timezone_offset_str})
        - Today: {today_date} | Tomorrow: {tomorrow_date}

        TIME EXAMPLES (current time {current_local_simple}):
        {time_examples}

        MULTILINGUAL TIME PARSING:
        Handle ALL time formats in ANY language including:
        - English: "today 1900", "at 7", "tonight", "7.30pm", "19h", "8ish", "after lunch", "end of day"
        - Portuguese: "hoje às 19h", "amanhã de manhã", "hoje à noite", "por volta das 8", "depois do almoço"
        - Spanish: "hoy a las 19h", "mañana por la mañana", "esta noche", "sobre las 8", "después de comer"
        - French: "aujourd'hui à 19h", "demain matin", "ce soir", "vers 8h", "après le déjeuner"
        - German: "heute um 19h", "morgen früh", "heute abend", "gegen 8", "nach dem Mittagessen"
        - Italian: "oggi alle 19", "domani mattina", "stasera", "verso le 8", "dopo pranzo"
        - Russian: "сегодня в 19:00", "завтра утром", "сегодня вечером", "около 8", "после обеда"

        PARSING RULES:
        1. PRIORITY: Explicit dates/times in message override all defaults
        2. ALL TIMES: Calculate in UTC directly - current UTC time is {current_utc_iso}
        3. Handle approximate times: "ish", "around", "about", "por volta", "sobre", "vers", "gegen", "verso", "около"
        4. Handle relative times: "tonight", "this evening", "end of day", "after lunch", "before work"
        5. Handle military time: "1900", "0800", "1430" (assume 24-hour format)
        6. Handle alternative formats: "7.30pm", "19h30", "7,30", "19:30h"
        7. Handle written numbers: "seven", "eight", "nine", "sete", "ocho", "sept", "sieben", "sette", "семь"
        8. If time has passed today, assume tomorrow (unless explicitly "today")
        9. No specific time = tomorrow 9AM local
        10. Output format MUST be: YYYY-MM-DDTHH:MM:SSZ

        TITLE: Create informative, specific titles (<50 chars).
        AVOID: "Decide on X", "Check with Y", "Handle appointment"

        DESCRIPTION:
        1. Brief action summary
        2. Full original conversation (with sender names)

        CONTENT PRIORITY:
        [CAPTION] → Primary instruction (title/timing)
        [SCREENSHOT TEXT] → Task details
        [SCREENSHOT DESCRIPTION] → Context only

        Message: {content_message}
        Owner: {owner_name}

        {format_instructions}
        """
        
        return PromptTemplate(
            template=template,
            input_variables=["content_message", "owner_name",
                           "current_utc_iso", "current_local_iso", "location",
                           "timezone_name", "timezone_offset_str",
                           "today_date", "tomorrow_date",
                           "current_local_simple", "time_examples"],
            partial_variables={"format_instructions": self.parser.get_format_instructions()}
        )
    
    def _calculate_precise_time(self, time_phrase: str, current_local: datetime, current_utc: datetime, offset_hours: int) -> Optional[str]:
        """Calculate precise time for common patterns - handles edge cases LLM struggles with."""
        import re
        
        # Pattern 1: "today X" times - flexible matching
        today_pattern = r'\btoday\s+(?:at\s+)?(?:(\d{1,2})(?::(\d{2}))?\s*(am|pm)?|noon|midnight)\b'
        match = re.search(today_pattern, time_phrase.lower())
        if match:
            if "noon" in time_phrase.lower():
                hour, minute = 12, 0
            elif "midnight" in time_phrase.lower():
                hour, minute = 0, 0
            else:
                hour_str = match.group(1)
                minute_str = match.group(2) or "00"
                am_pm = match.group(3)
                
                if not hour_str:
                    return None
                
                hour = int(hour_str)
                minute = int(minute_str)
                
                # Handle AM/PM conversion, but only if AM/PM is specified
                if am_pm:
                    if am_pm.lower() == 'pm' and hour != 12:
                        hour += 12
                    elif am_pm.lower() == 'am' and hour == 12:
                        hour = 0
                # If no AM/PM specified, assume 24-hour format if hour > 12
                # or if hour is reasonable (e.g., 15:00 means 3pm)
            
            # Create target time for today
            target_local = current_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
            
            # If time has passed, it means tomorrow
            if target_local <= current_local:
                target_local += timedelta(days=1)
            
            # Convert to UTC
            target_utc = target_local - timedelta(hours=offset_hours)
            return target_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        
        # Pattern 2: "tomorrow X" times - flexible matching
        tomorrow_pattern = r'\btomorrow\s+(?:at\s+)?(?:(\d{1,2})(?::(\d{2}))?\s*(am|pm)?|noon|midnight)\b'
        match = re.search(tomorrow_pattern, time_phrase.lower())
        if match:
            if "noon" in time_phrase.lower():
                hour, minute = 12, 0
            elif "midnight" in time_phrase.lower():
                hour, minute = 0, 0
            else:
                hour_str = match.group(1)
                minute_str = match.group(2) or "00"
                am_pm = match.group(3)
                
                if not hour_str:
                    return None
                
                hour = int(hour_str)
                minute = int(minute_str)
                
                # Handle AM/PM conversion, but only if AM/PM is specified
                if am_pm:
                    if am_pm.lower() == 'pm' and hour != 12:
                        hour += 12
                    elif am_pm.lower() == 'am' and hour == 12:
                        hour = 0
                # If no AM/PM specified, assume 24-hour format if hour > 12
                # or if hour is reasonable (e.g., 15:00 means 3pm)
            
            # Create target time for tomorrow
            target_local = (current_local + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
            
            # Convert to UTC
            target_utc = target_local - timedelta(hours=offset_hours)
            return target_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        
        # Pattern 3: Relative times "in X minutes/hours/days/weeks" and "Xm/Xh from now"
        relative_pattern = r'(?:\bin\s+(\d+)\s+(minute|minutes|hour|hours|day|days|week|weeks)\b|\bin\s+a\s+(day|week)\b|(\d+)\s*(m|min|h|hour|hours|d|day|days|w|week|weeks)\s*(?:from\s+now)?)'
        match = re.search(relative_pattern, time_phrase.lower())
        if match:
            if match.group(1):  # "in X minutes/hours/days/weeks" format
                amount = int(match.group(1))
                unit = match.group(2)
            elif match.group(3):  # "in a day/week" format
                amount = 1
                unit = match.group(3)
            else:  # "Xm/Xh/Xd/Xw from now" format
                amount = int(match.group(4))
                unit = match.group(5)
            
            if 'h' in unit or 'hour' in unit:
                delta = timedelta(hours=amount)
            elif 'd' in unit or 'day' in unit:
                delta = timedelta(days=amount)
            elif 'w' in unit or 'week' in unit:
                delta = timedelta(weeks=amount)
            else:
                delta = timedelta(minutes=amount)
            
            target_utc = current_utc + delta
            return target_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        
        # Pattern 4: "asap", "now"
        if any(word in time_phrase.lower() for word in ['asap', 'now', 'immediately']):
            target_utc = current_utc + timedelta(hours=1)
            return target_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        
        # Pattern 5: "at HH:MM" or "at H PM/AM" patterns
        at_time_pattern = r'\bat\s+(?:(\d{1,2})(?::(\d{2}))?\s*(am|pm)?)\b'
        match = re.search(at_time_pattern, time_phrase.lower())
        if match:
            hour_str = match.group(1)
            minute_str = match.group(2) or "00"
            am_pm = match.group(3)
            
            if hour_str:
                hour = int(hour_str)
                minute = int(minute_str)
                
                # Handle AM/PM conversion
                if am_pm:
                    if am_pm.lower() == 'pm' and hour != 12:
                        hour += 12
                    elif am_pm.lower() == 'am' and hour == 12:
                        hour = 0
                
                # Create target time for today
                target_local = current_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
                
                # If time has already passed today, assume tomorrow
                if target_local <= current_local:
                    target_local += timedelta(days=1)
                
                # Convert to UTC
                target_utc = target_local - timedelta(hours=offset_hours)
                return target_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        
        # Pattern 6: Month/day patterns (Nov 25, Dec 1, etc.)
        month_pattern = r'\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(\d{1,2})\b'
        match = re.search(month_pattern, time_phrase.lower())
        if match:
            month_abbr = match.group(1)
            day = int(match.group(2))
            
            month_map = {
                'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
                'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12
            }
            month = month_map.get(month_abbr)
            
            if month:
                # Check if this date has already passed this year
                try:
                    target_date = current_local.replace(month=month, day=day, hour=9, minute=0, second=0, microsecond=0)
                    if target_date.date() <= current_local.date():
                        # Use next year
                        target_date = target_date.replace(year=current_local.year + 1)
                    
                    # Convert to UTC
                    target_utc = target_date - timedelta(hours=offset_hours)
                    return target_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
                except ValueError:
                    # Invalid date (e.g., Feb 30)
                    pass
        
        return None
    
    def _generate_time_examples(self, current_local: datetime, current_utc: datetime, offset_hours: int) -> str:
        """Generate clear, non-confusing time interpretation examples."""
        examples = []
        
        # Simple, clear examples that don't cause confusion
        tomorrow = current_local + timedelta(days=1)
        
        examples.extend([
            f'- "in 1 hour" → {(current_utc + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")}',
            f'- "in 30 minutes" → {(current_utc + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")}',
            f'- "tomorrow 9am" → {(tomorrow.replace(hour=9, minute=0, second=0) - timedelta(hours=offset_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")}',
            f'- "asap" → {(current_utc + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")}'
        ])
        
        return '\n'.join(examples)
    
    def _get_timezone_name(self, location: str) -> str:
        """Get a friendly timezone name for a location."""
        if not location:
            return "UTC"
        
        location_lower = location.lower()
        
        # Common timezone name mappings
        timezone_names = {
            'portugal': 'Portugal Time',
            'cascais': 'Portugal Time',
            'lisbon': 'Portugal Time',
            'porto': 'Portugal Time',
            'uk': 'UK Time',
            'united kingdom': 'UK Time', 
            'london': 'UK Time',
            'spain': 'Spain Time',
            'madrid': 'Spain Time',
            'barcelona': 'Spain Time',
            'france': 'France Time',
            'paris': 'France Time',
            'germany': 'Germany Time',
            'berlin': 'Germany Time',
            'new york': 'Eastern Time',
            'california': 'Pacific Time',
            'tokyo': 'Japan Time',
            'sydney': 'Australia Time'
        }
        
        # Check for matches
        for key, name in timezone_names.items():
            if key in location_lower:
                return name
        
        # Default to location name + " Time"
        return f"{location} Time"

    def parse_content_to_task(self, content_message: str, owner_name: Optional[str] = None, 
                             location: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Parse content message into a structured task.
        
        Args:
            content_message: The message content to parse
            owner_name: Name of the task owner
            location: User's location for timezone context
            
        Returns:
            Dictionary with task data or None if parsing fails
            
        Raises:
            ParsingError: If parsing fails
        """
        try:
            # Get current times
            current_utc = datetime.now(timezone.utc)
            
            # Calculate user's local time and timezone offset
            if location:
                offset_hours = self.get_timezone_offset(location)
                user_local_time = current_utc + timedelta(hours=offset_hours)
                timezone_name = self._get_timezone_name(location)
            else:
                offset_hours = 0
                user_local_time = current_utc
                timezone_name = "UTC"
                location = "UTC"
            
            # Format timezone offset string (e.g., "+1" or "-5")
            timezone_offset_str = f"+{offset_hours}" if offset_hours >= 0 else str(offset_hours)
            
            # LLM-FIRST APPROACH: Use LLM for ALL time parsing to handle multilingual and edge cases
            # Static patterns are kept as fallback only in case LLM fails
            logger.info(f"Using LLM-first parsing for: {content_message}")
            content_with_time = content_message
            
            # Generate dynamic time examples
            time_examples = self._generate_time_examples(user_local_time, current_utc, offset_hours)
            
            # Prepare all the variables for the prompt
            input_data = {
                "content_message": content_with_time,
                "owner_name": owner_name or "User",
                "current_utc_iso": current_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "current_local_iso": user_local_time.strftime("%Y-%m-%dT%H:%M:%S"),
                "location": location,
                "timezone_name": timezone_name,
                "timezone_offset_str": timezone_offset_str,
                "today_date": user_local_time.strftime("%Y-%m-%d"),
                "tomorrow_date": (user_local_time + timedelta(days=1)).strftime("%Y-%m-%d"),
                "current_local_simple": user_local_time.strftime("%H:%M"),
                "time_examples": time_examples
            }
            
            # Format the prompt
            prompt_text = self.prompt_template.format(**input_data)
            logger.debug(f"LLM Input: {prompt_text}")
            
            # Call the language model
            output = self.llm.invoke([HumanMessage(content=prompt_text)])
            logger.debug(f"LLM Output: {output.content}")
            
            # Parse the output
            parsed_task = self.parser.parse(output.content)
            logger.debug(f"Parsed task: {parsed_task}")
            
            result = parsed_task.model_dump()
            
            # LLM handles all time parsing - no static overrides
            logger.info(f"Successfully parsed task: {result['title']} with LLM-parsed time: {result['due_time']}")
            return result
            
        except Exception as e:
            logger.error(f"LLM parsing failed, trying static fallback: {e}")
            
            # FALLBACK: Try static patterns only if LLM completely fails
            try:
                precise_time = self._calculate_precise_time(content_message, user_local_time, current_utc, offset_hours)
                if precise_time:
                    logger.info(f"Using static fallback for time pattern in: {content_message}")
                    # Create a minimal task with static time
                    fallback_result = {
                        "title": content_message[:50],  # Truncate for title
                        "due_time": precise_time,
                        "description": content_message
                    }
                    logger.info(f"Static fallback successful: {fallback_result['title']}")
                    return fallback_result
                else:
                    logger.error(f"Static fallback also failed - no patterns matched")
                    raise ParsingError(f"Both LLM and static parsing failed: {e}")
            except Exception as fallback_e:
                logger.error(f"Static fallback failed: {fallback_e}")
                raise ParsingError(f"Content parsing failed: {e}")
    
    def _get_timezone_info(self, location: Optional[str]) -> str:
        """Get timezone information for a location."""
        if not location:
            return "UTC+0"
        
        location_lower = location.lower()
        
        # Common timezone mappings
        timezone_map = {
            # Portugal
            'portugal': 'UTC+1 (UTC+2 during DST)',
            'cascais': 'UTC+1 (UTC+2 during DST)', 
            'lisbon': 'UTC+1 (UTC+2 during DST)',
            'porto': 'UTC+1 (UTC+2 during DST)',
            
            # UK
            'uk': 'UTC+0 (UTC+1 during DST)',
            'united kingdom': 'UTC+0 (UTC+1 during DST)',
            'london': 'UTC+0 (UTC+1 during DST)',
            
            # Spain
            'spain': 'UTC+1 (UTC+2 during DST)',
            'madrid': 'UTC+1 (UTC+2 during DST)',
            'barcelona': 'UTC+1 (UTC+2 during DST)',
            
            # France
            'france': 'UTC+1 (UTC+2 during DST)',
            'paris': 'UTC+1 (UTC+2 during DST)',
            
            # Germany
            'germany': 'UTC+1 (UTC+2 during DST)',
            'berlin': 'UTC+1 (UTC+2 during DST)',
            
            # USA East Coast
            'new york': 'UTC-5 (UTC-4 during DST)',
            'est': 'UTC-5 (UTC-4 during DST)',
            'eastern': 'UTC-5 (UTC-4 during DST)',
            
            # USA West Coast  
            'california': 'UTC-8 (UTC-7 during DST)',
            'pst': 'UTC-8 (UTC-7 during DST)',
            'pacific': 'UTC-8 (UTC-7 during DST)',
        }
        
        # Check for exact matches first
        for key, tz in timezone_map.items():
            if key in location_lower:
                return tz
        
        # Default fallback
        return "UTC+0 (please specify timezone for accuracy)"
    
    def get_timezone_offset(self, location: Optional[str]) -> int:
        """Get dynamic timezone offset in hours for a location (handles DST automatically)."""
        if not location:
            return 0
        
        location_lower = location.lower().strip()
        
        # First try common location mappings for known cities/countries
        timezone_map = {
            # Portugal/Spain/France/Germany (Central European Time)
            'portugal': 'Europe/Lisbon',
            'cascais': 'Europe/Lisbon', 
            'lisbon': 'Europe/Lisbon',
            'porto': 'Europe/Lisbon',
            'spain': 'Europe/Madrid',
            'madrid': 'Europe/Madrid',
            'barcelona': 'Europe/Madrid',
            'france': 'Europe/Paris',
            'paris': 'Europe/Paris',
            'germany': 'Europe/Berlin',
            'berlin': 'Europe/Berlin',
            
            # UK
            'uk': 'Europe/London',
            'united kingdom': 'Europe/London',
            'london': 'Europe/London',
            
            # USA
            'new york': 'America/New_York',
            'est': 'America/New_York', 
            'eastern': 'America/New_York',
            'california': 'America/Los_Angeles',
            'pst': 'America/Los_Angeles',
            'pacific': 'America/Los_Angeles',
            
            # Additional major cities
            'tokyo': 'Asia/Tokyo',
            'japan': 'Asia/Tokyo',
            'sydney': 'Australia/Sydney',
            'australia': 'Australia/Sydney',
            'moscow': 'Europe/Moscow',
            'russia': 'Europe/Moscow',
            'beijing': 'Asia/Shanghai',
            'china': 'Asia/Shanghai',
            'india': 'Asia/Kolkata',
            'mumbai': 'Asia/Kolkata',
            'delhi': 'Asia/Kolkata',
            'dubai': 'Asia/Dubai',
            'uae': 'Asia/Dubai',
        }
        
        # Find timezone identifier from known mappings
        tz_identifier = None
        for key, tz_id in timezone_map.items():
            if key in location_lower:
                tz_identifier = tz_id
                break
        
        # If not found in mappings, try to guess from common timezone patterns
        if not tz_identifier:
            tz_identifier = self._guess_timezone_from_location(location_lower)
        
        if not tz_identifier:
            logger.warning(f"Could not determine timezone for location: {location}")
            return 0  # Default to UTC
        
        try:
            # Get the timezone and calculate current offset
            tz = zoneinfo.ZoneInfo(tz_identifier)
            current_utc = datetime.now(timezone.utc)
            local_time = current_utc.astimezone(tz)
            
            # Calculate offset in hours with overflow protection
            try:
                offset_seconds = local_time.utcoffset().total_seconds()
                offset_hours_float = offset_seconds / 3600
                
                # Bounds check to prevent C int overflow and unreasonable offsets
                if abs(offset_hours_float) >= 24:
                    logger.warning(f"Unusual timezone offset calculated: {offset_hours_float}h for {location}. Using UTC.")
                    return 0
                
                offset_hours = int(offset_hours_float)
                
            except (OverflowError, ValueError) as overflow_e:
                logger.warning(f"Overflow in timezone calculation for {location}: {overflow_e}. Using UTC.")
                return 0
            
            logger.debug(f"Timezone for {location}: {tz_identifier} (offset: {offset_hours}h)")
            return offset_hours
            
        except Exception as e:
            logger.error(f"Error calculating timezone offset for {location}: {e}")
            return 0  # Default to UTC
    
    def _guess_timezone_from_location(self, location_lower: str) -> Optional[str]:
        """Try to guess timezone identifier from location name."""
        # Try common timezone identifier patterns
        common_zones = [
            # Try direct timezone format (e.g., "europe/paris")
            location_lower.replace(' ', '_').replace('/', '_'),
            
            # Try continent/city format
            f"Europe/{location_lower.title()}",
            f"America/{location_lower.title()}",
            f"Asia/{location_lower.title()}",
            f"Africa/{location_lower.title()}",
            f"Australia/{location_lower.title()}",
            
            # Try major city variations (using more specific patterns)
            f"America/New_York" if 'new york' in location_lower or location_lower in ['nyc', 'ny'] else None,
            f"America/Los_Angeles" if 'los angeles' in location_lower or location_lower in ['la', 'los_angeles'] else None,
            f"Europe/London" if 'london' in location_lower else None,
        ]
        
        # Test each potential timezone
        for tz_id in common_zones:
            if tz_id:
                try:
                    zoneinfo.ZoneInfo(tz_id)
                    return tz_id
                except:
                    continue
        
        return None
    
    def convert_utc_to_local_display(self, utc_time_str: str, location: Optional[str]) -> str:
        """Convert UTC time string to local time for display."""
        try:
            from datetime import datetime, timezone, timedelta
            from dateutil import parser as date_parser
            
            # Parse UTC time
            utc_time = date_parser.isoparse(utc_time_str)
            if utc_time.tzinfo is None:
                utc_time = utc_time.replace(tzinfo=timezone.utc)
            
            # Get timezone offset
            offset_hours = self.get_timezone_offset(location)
            
            # Convert to local time
            local_time = utc_time + timedelta(hours=offset_hours)
            
            # Format for display
            if location and any(loc in location.lower() for loc in ['portugal', 'cascais', 'lisbon']):
                timezone_name = "Portugal time"
            elif location and any(loc in location.lower() for loc in ['uk', 'london']):
                timezone_name = "UK time"
            elif location:
                timezone_name = f"{location} time"
            else:
                timezone_name = "local time"
            
            return f"{local_time.strftime('%B %d, %Y at %H:%M')} ({timezone_name})"
            
        except Exception as e:
            logger.error(f"Error converting time for display: {e}")
            # Fallback to simple string format
            return f"Error parsing time: {utc_time_str} (UTC)"

# Remove global instance - use DI container instead