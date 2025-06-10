"""Text parsing service using LangChain and OpenAI."""

from typing import Optional, Dict, Any
from datetime import datetime, timezone

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
            temperature=0,
            max_tokens=None,
            openai_api_key=config.OPENAI_API_KEY
        )
        
        self.parser = PydanticOutputParser(pydantic_object=TaskCreate)
        self.prompt_template = self._create_prompt_template()
    
    def _create_prompt_template(self) -> PromptTemplate:
        """Create the prompt template for task parsing."""
        template = """
        You are an assistant that creates a task from the provided conversation.

        The task should include a 'title', a 'due_time' in UTC ISO 8601 format (YYYY-MM-DDTHH:MM:SSZ), and a 'description'. 

        CRITICAL DATE/TIME PARSING - READ CAREFULLY:
        - First, check if there's a specific date/time mentioned in the conversation - this should ALWAYS take precedence
        - Look for phrases like "tomorrow", "next Monday", "on June 12", "at 3PM", "12/Jun", etc.
        - IMPORTANT: "12/Jun" means DAY 12 of JUNE, which is June 12th, NOT June 11th
        - "12/Jun" = "June 12" = "12th day of June" = the 12th day of the month
        - When parsing dates like "12/Jun" or "June 12", ALWAYS use the CURRENT YEAR ({current_year}) unless explicitly stated otherwise
        - If a date/time is explicitly mentioned, use that exact date/time for scheduling
        
        Only if no date/time is mentioned at all:
        - For small tasks (e.g., "remember to buy milk") schedule one hour from now
        - For larger tasks, schedule for tomorrow at 9AM

        Given the content message conversation, determine the most appropriate and informative title unless it's explicitly specified. The title should be informative, concrete, and not verbose (bad examples are "Decide on appointment" or "Check task good" or "Check with Iryna regarding furniture", "Decide whether to have a massage").
        Description should contain summarization of things to do and copy of the original conversation (with line breaks). 
        Usually but not always first message of conversation contains task-related instruction (e.g., time, and/or full title or  tip for the title)
        
        CRITICAL TIMEZONE HANDLING:
        - The user is located in: {location}
        - Current year is: {current_year}
        - When a time is mentioned (like "12:00"), interpret it as LOCAL TIME in the user's location
        - Convert local time to UTC for the due_time field
        - For Portugal/Cascais: Local time is UTC+1 (or UTC+2 during DST)
        - Example: If user says "12:00" and they're in Portugal, the UTC time should be "11:00" (12:00 - 1 hour)
        - Example: "12/Jun" means "June 12, {current_year}" not any other year
        - Example: "12/Jun, 12:00" in Portugal = "2025-06-12T11:00:00Z" (June 12th at 11:00 UTC)
        - Always use the current year {current_year} unless explicitly stated otherwise
        
        DOUBLE-CHECK YOUR DATE PARSING:
        - "12/Jun" = Day 12 of June = June 12th = 2025-06-12
        - NOT June 11th, NOT any other date
        
        Content Message: {content_message}
        Current UTC time: {cur_time}
        Task Owner Name: {owner_name}

        {format_instructions}
        """
        
        return PromptTemplate(
            template=template,
            input_variables=["content_message", "cur_time", "owner_name", "location", "current_year"],
            partial_variables={"format_instructions": self.parser.get_format_instructions()}
        )
    
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
            # Prepare input data with enhanced timezone info
            current_utc = datetime.now(timezone.utc)
            timezone_info = self._get_timezone_info(location)
            
            input_data = {
                "content_message": content_message,
                "cur_time": current_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
                "owner_name": owner_name or "User",
                "location": f"{location or 'UTC'} (Current timezone: {timezone_info})",
                "current_year": current_utc.year
            }
            
            # Format the prompt
            prompt_text = self.prompt_template.format(**input_data)
            logger.debug(f"LLM Input: {prompt_text}")
            
            # Call the language model
            output = self.llm([HumanMessage(content=prompt_text)])
            logger.debug(f"LLM Output: {output.content}")
            
            # Parse the output
            parsed_task = self.parser.parse(output.content)
            logger.debug(f"Parsed task: {parsed_task}")
            
            result = parsed_task.dict()
            logger.info(f"Successfully parsed task: {result['title']}")
            return result
            
        except Exception as e:
            logger.error(f"Failed to parse content to task: {e}")
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
        """Get timezone offset in hours for a location."""
        if not location:
            return 0
        
        location_lower = location.lower()
        
        # Basic timezone offset mappings (assuming standard time, not DST)
        offset_map = {
            # Portugal/Spain/France/Germany (Central European Time)
            'portugal': 1, 'cascais': 1, 'lisbon': 1, 'porto': 1,
            'spain': 1, 'madrid': 1, 'barcelona': 1,
            'france': 1, 'paris': 1,
            'germany': 1, 'berlin': 1,
            
            # UK (Greenwich Mean Time)
            'uk': 0, 'united kingdom': 0, 'london': 0,
            
            # USA
            'new york': -5, 'est': -5, 'eastern': -5,
            'california': -8, 'pst': -8, 'pacific': -8,
        }
        
        # Check for exact matches first
        for key, offset in offset_map.items():
            if key in location_lower:
                return offset
        
        return 0  # Default to UTC
    
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