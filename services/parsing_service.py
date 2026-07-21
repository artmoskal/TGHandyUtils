"""Text parsing service using LangChain and OpenAI."""

from typing import Optional, Dict, Any
from datetime import datetime, timezone, timedelta
import zoneinfo

from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.messages import HumanMessage, SystemMessage

from models.task import TaskCreate
from core.interfaces import IParsingService, IConfig, IUserPreferencesRepository
from core.exceptions import ParsingError
from core.logging import get_logger
from services.llm_factory import create_anki_chat_model, llm_cost_class, llm_provider_label
from ai_workflow_engine.prompt_loader import load_prompt_template
from ai_workflow_engine.usage import invoke_metered_chat

logger = get_logger(__name__)

class ParsingService(IParsingService):
    """Service for parsing text into structured task data."""

    # Class variable to track token usage across all instances
    _token_usage = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "call_count": 0
    }
    
    def __init__(self, config: IConfig, preferences_repo: IUserPreferencesRepository = None):
        self.config = config
        self.preferences_repo = preferences_repo

        # No eager OPENAI_API_KEY gate here (codex 2026-07-21): the routed factory fails
        # loudly when a METERED OpenAI client is actually constructed without a key, and
        # registry backends (chatgpt-web / claude-p) must run without one.
        # Temperature/cache/base-url handled centrally by the factory (0.0 = max precision).
        # ROUTED door: registry backends (chatgpt-web / claude-p / ...) must never be
        # sent to the metered OpenAI client as if they were API model ids.
        self.llm = create_anki_chat_model(config, model=self._model_name(), temperature=0.0)
        
        self.parser = PydanticOutputParser(pydantic_object=TaskCreate)
        self.prompt_template = self._create_prompt_template()
        self.static_prompt_template = self._create_static_prompt_template()
        self.dynamic_prompt_template = self._create_dynamic_prompt_template()

    def _model_name(self) -> str:
        model = getattr(self.config, "TASK_PARSING_MODEL", None)
        if not isinstance(model, str) or not model.strip():
            return "gpt-5.4-mini"
        return model
    
    def _create_prompt_template(self) -> PromptTemplate:
        """Create the prompt template for task parsing."""
        template = load_prompt_template("tasks/task_create.full.prompt")
        
        return PromptTemplate(
            template=template,
            input_variables=["content_message", "owner_name",
                           "current_local_time", "today_date", "tomorrow_date", "is_late_night"],
            partial_variables={"format_instructions": self.parser.get_format_instructions()}
        )

    def _create_static_prompt_template(self) -> PromptTemplate:
        """Create the cacheable static prompt prefix for task parsing."""
        template = load_prompt_template("tasks/task_create.static.prompt")
        return PromptTemplate(
            template=template,
            input_variables=[],
            partial_variables={"format_instructions": self.parser.get_format_instructions()},
        )

    def _create_dynamic_prompt_template(self) -> PromptTemplate:
        """Create the dynamic prompt tail for task parsing."""
        template = load_prompt_template("tasks/task_create.dynamic.prompt")
        return PromptTemplate(
            template=template,
            input_variables=[
                "content_message",
                "owner_name",
                "current_local_time",
                "today_date",
                "tomorrow_date",
                "is_late_night",
            ],
        )
    
    def _calculate_precise_time(self, time_phrase: str, current_local: datetime, current_utc: datetime, offset_hours: int) -> Optional[str]:
        """Calculate precise time for common patterns - handles edge cases LLM struggles with."""
        import re
        
        # Pattern 1: "today X" times - strict matching for proper time formats
        # Either: HH:MM (with exactly 2 digits for minutes), or HH am/pm, or special words
        today_pattern = r'\btoday\s+(?:at\s+)?(?:(\d{1,2}):(\d{2})\s*(am|pm)?|(\d{1,2})\s*(am|pm)|noon|midnight)\b'
        match = re.search(today_pattern, time_phrase.lower())
        if match:
            if "noon" in time_phrase.lower():
                hour, minute = 12, 0
            elif "midnight" in time_phrase.lower():
                hour, minute = 0, 0
            else:
                # Check which pattern matched
                if match.group(1):  # HH:MM format
                    hour_str = match.group(1)
                    minute_str = match.group(2)
                    am_pm = match.group(3)
                else:  # HH am/pm format
                    hour_str = match.group(4)
                    minute_str = "00"
                    am_pm = match.group(5)
                
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
            
            # For "today", if the time has already passed, it still means today
            # Don't push to tomorrow - user explicitly said "today"
            
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
                # Check which pattern matched
                hour_str = match.group(1)
                minute_str = match.group(2) if match.group(2) else "00"
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
        # Note: Single-letter units (h, m, d, w) require "from now" to avoid matching time formats like "19h"
        relative_pattern = r'(?:\bin\s+(\d+)\s+(minute|minutes|hour|hours|day|days|week|weeks)\b|\bin\s+a\s+(day|week)\b|(\d+)\s*(min|hour|hours|day|days|week|weeks)\s+from\s+now|(\d+)\s*([hmdw])\s+from\s+now)'
        match = re.search(relative_pattern, time_phrase.lower())
        if match:
            if match.group(1):  # "in X minutes/hours/days/weeks" format
                amount = int(match.group(1))
                unit = match.group(2)
            elif match.group(3):  # "in a day/week" format
                amount = 1
                unit = match.group(3)
            elif match.group(4):  # "X min/hour/hours/day/days/week/weeks from now" format
                amount = int(match.group(4))
                unit = match.group(5)
            else:  # "Xh/Xm/Xd/Xw from now" format (single letter units)
                amount = int(match.group(6))
                unit = match.group(7)
            
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
        
        # Pattern 5: "at HH:MM" or "at H am/pm" patterns - require either full time or am/pm
        at_time_pattern = r'\bat\s+(?:(\d{1,2}):(\d{2})(?:\s*(am|pm))?|(\d{1,2})\s*(am|pm))\b'
        match = re.search(at_time_pattern, time_phrase.lower())
        if match:
            # Check which pattern matched
            if match.group(1):  # HH:MM format
                hour_str = match.group(1)
                minute_str = match.group(2)
                am_pm = match.group(3)
            else:  # H am/pm format
                hour_str = match.group(4)
                minute_str = "00"
                am_pm = match.group(5)
            
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

    def parse_content_to_task(self, content_message: str, owner_name: Optional[str] = None, 
                             location: Optional[str] = None, user_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Parse content message into a structured task.
        
        Args:
            content_message: The message content to parse
            owner_name: Name of the task owner
            location: User's location for timezone context
            user_id: User ID for preferences lookup
            
        Returns:
            Dictionary with task data or None if parsing fails
            
        Raises:
            ParsingError: If parsing fails
        """
        try:
            # Get current UTC time
            current_utc = datetime.now(timezone.utc)
            
            # Get or calculate UTC offset
            offset_hours = 0
            if self.preferences_repo and user_id:
                user_prefs = self.preferences_repo.get_preferences(user_id)
                if user_prefs and user_prefs.utc_offset is not None:
                    offset_hours = user_prefs.utc_offset
                    logger.debug(f"Using cached UTC offset: {offset_hours}")
                elif user_prefs and user_prefs.location:
                    # Calculate from location and cache it
                    offset_hours = self.get_timezone_offset(user_prefs.location)
                    from models.unified_recipient import UnifiedUserPreferencesUpdate
                    self.preferences_repo.update_preferences(
                        user_id, 
                        UnifiedUserPreferencesUpdate(utc_offset=offset_hours)
                    )
                    logger.info(f"Calculated and cached UTC offset: {offset_hours}")
            elif location:
                # Fallback to location-based calculation
                offset_hours = self.get_timezone_offset(location)
                logger.debug(f"Calculated UTC offset from location: {offset_hours}")
            
            # Calculate user's local time (no timezone info for LLM)
            user_local_time = current_utc + timedelta(hours=offset_hours)
            
            # Prepare simplified prompt variables (no timezone info)
            input_data = {
                "content_message": content_message,
                "owner_name": owner_name or "User",
                "current_local_time": user_local_time.strftime("%Y-%m-%d %H:%M:%S"),
                "today_date": user_local_time.strftime("%Y-%m-%d"),
                "tomorrow_date": (user_local_time + timedelta(days=1)).strftime("%Y-%m-%d"),
                "is_late_night": "Yes" if user_local_time.hour >= 23 else "No",
            }
            
            # Format cache-friendly messages: stable rules/schema first, request context last.
            system_prompt = self.static_prompt_template.format()
            user_prompt = self.dynamic_prompt_template.format(**input_data)
            prompt_text = f"{system_prompt}\n\n{user_prompt}"
            logger.debug(f"LLM Input (timezone-agnostic): {prompt_text}")
            
            # Call the language model
            output = invoke_metered_chat(
                self.llm,
                [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)],
                node="task_parser",
                model=self._model_name(),
                # the backend registry knows whether this model is metered or rides a
                # subscription — phantom metered $0 would break cost honesty.
                cost_class=llm_cost_class(self._model_name(), self.config),
                provider=llm_provider_label(self._model_name()),
            )
            logger.debug(f"LLM Output: {output.content}")
            
            # Track token usage
            if hasattr(output, 'response_metadata') and 'token_usage' in output.response_metadata:
                usage = output.response_metadata['token_usage']
                ParsingService._token_usage['prompt_tokens'] += usage.get('prompt_tokens', 0)
                ParsingService._token_usage['completion_tokens'] += usage.get('completion_tokens', 0)
                ParsingService._token_usage['total_tokens'] += usage.get('total_tokens', 0)
                ParsingService._token_usage['call_count'] += 1
                logger.debug(f"Token usage - Prompt: {usage.get('prompt_tokens', 0)}, "
                           f"Completion: {usage.get('completion_tokens', 0)}, "
                           f"Total: {usage.get('total_tokens', 0)}")
            
            # Parse the output
            parsed_task = self.parser.parse(output.content)
            logger.debug(f"Parsed task: {parsed_task}")
            
            result = parsed_task.model_dump()
            
            # Convert local time from LLM to UTC
            local_due_time = datetime.fromisoformat(result['due_time'])
            utc_due_time = local_due_time - timedelta(hours=offset_hours)
            result['due_time'] = utc_due_time.strftime("%Y-%m-%dT%H:%M:%SZ")
            
            # Ensure the task is at least 35 seconds in the future
            parsed_due_time = datetime.fromisoformat(result['due_time'].replace('Z', '+00:00'))
            min_future_time = current_utc + timedelta(seconds=35)
            if parsed_due_time <= min_future_time:
                # If the time is in the past or too close to now, push it to tomorrow at the same time
                result['due_time'] = (parsed_due_time + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
                logger.info(f"Pushed task to tomorrow as it was too close to current time")
            
            logger.info(f"Successfully parsed task: {result['title']} with timezone-agnostic approach")
            return result
            
        except Exception as e:
            logger.error(f"LLM parsing failed, trying static fallback: {e}")
            
            # FALLBACK: Try static patterns only if LLM completely fails
            try:
                # Ensure we have current_utc
                if 'current_utc' not in locals():
                    current_utc = datetime.now(timezone.utc)
                
                # Calculate local time for static patterns
                if not 'user_local_time' in locals():
                    if location:
                        offset_hours = self.get_timezone_offset(location)
                    else:
                        offset_hours = 0
                    user_local_time = current_utc + timedelta(hours=offset_hours)
                
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
                    raise ParsingError("Both LLM and static parsing failed")
            except ParsingError:
                # Re-raise ParsingError as-is
                raise
            except Exception as fallback_e:
                logger.error(f"Static fallback failed: {fallback_e}")
                raise ParsingError(f"Content parsing failed: {e}")
    
    def parse_timezone_with_llm(self, location: str) -> int:
        """Parse location string to UTC offset using LLM.
        
        Args:
            location: Location string (e.g. "NY", "Portugal", "London")
            
        Returns:
            UTC offset in hours (e.g. -5 for NY, 0 for London)
        """
        if not location:
            return 0
            
        try:
            system_prompt = """Determine the UTC timezone offset for a user-provided location.

IMPORTANT: Return ONLY a single number representing the UTC offset in hours.
Consider daylight saving time if currently active.

Examples:
- "NY" or "New York" → -5 (or -4 during DST)
- "London" or "UK" → 0 (or 1 during BST)
- "PT" or "Portugal" → 0 (or 1 during summer)
- "California" or "LA" → -8 (or -7 during DST)
- "Tokyo" → 9
- "Sydney" → 10 (or 11 during DST)
"""

            user_prompt = f"""Location: {location}
UTC offset (hours):"""

            # Create a simple LLM call without complex parsing
            response = invoke_metered_chat(
                self.llm,
                [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)],
                node="timezone_offset_parser",
                model=self._model_name(),
                cost_class=llm_cost_class(self._model_name(), self.config),
                provider=llm_provider_label(self._model_name()),
            )
            offset_str = response.content.strip()
            
            # Track token usage
            if hasattr(response, 'response_metadata') and 'token_usage' in response.response_metadata:
                usage = response.response_metadata['token_usage']
                ParsingService._token_usage['prompt_tokens'] += usage.get('prompt_tokens', 0)
                ParsingService._token_usage['completion_tokens'] += usage.get('completion_tokens', 0)
                ParsingService._token_usage['total_tokens'] += usage.get('total_tokens', 0)
                ParsingService._token_usage['call_count'] += 1
            
            # Parse the response - should be a simple number
            try:
                offset = int(offset_str)
                # Validate reasonable range
                if -12 <= offset <= 14:  # Valid UTC offsets
                    logger.info(f"LLM parsed location '{location}' to UTC offset {offset}")
                    return offset
                else:
                    logger.warning(f"LLM returned invalid offset {offset} for location '{location}'")
                    return 0
            except ValueError:
                logger.warning(f"LLM returned non-numeric offset '{offset_str}' for location '{location}'")
                return 0
                
        except Exception as e:
            logger.error(f"LLM timezone parsing failed for '{location}': {e}")
            # Fall back to existing hardcoded method
            return self.get_timezone_offset(location)
    
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
    
    @classmethod
    def get_token_usage(cls) -> dict:
        """Get current token usage statistics."""
        return cls._token_usage.copy()
    
    @classmethod
    def reset_token_usage(cls):
        """Reset token usage statistics."""
        cls._token_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "call_count": 0
        }

# Remove global instance - use DI container instead
