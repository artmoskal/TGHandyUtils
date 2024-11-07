from langchain import OpenAI
from langchain.prompts import PromptTemplate
from langchain.output_parsers import PydanticOutputParser
from langchain.chat_models import ChatOpenAI
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field
import os
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

if not OPENAI_API_KEY:
    raise ValueError("Please set OPENAI_API_KEY in the .env file.")


# Define the schema for the task using Pydantic
class Task(BaseModel):
    title: str = Field(description="The title of the task.")
    due_time: str = Field(description="The due time in UTC ISO 8601 format (YYYY-MM-DDTHH:MM:SSZ).")
    description: str = Field(description="The description or details of the task.")


# Function to parse task descriptions using LangChain
# Added sender information to adjust the prompt for different task formulations
def parse_description_with_langchain(content_message=None, owner_name=None, location=None):
    parser_lc = PydanticOutputParser(pydantic_object=Task)

    # Create a prompt template to instruct the AI model
    prompt_template = """
    You are an assistant that creates a task from the provided conversation.

    The task should include a 'title', a 'due_time' in UTC ISO 8601 format (YYYY-MM-DDTHH:MM:SSZ), and a 'description'.

    Given the content message conversation, determine the most appropriate and informative title unless it's explicitly specified. The title should be informative, concrete, and not verbose (bad are "Decide on appointment" or "Check task good" are "Check with Iryna regarding furniture", "Decide whether to have a massage".
    Description should contain summarization of things to do and copy of the original conversation (with line breaks). 
    Usually but not always first message of conversation contains task-related instruction (e.g., time, and/or full title or  tip for the title)
    Use the context of this conversation, considering {location} hour(s) offset from UTC due to the user's location.
    
    Content Message: {content_message}
    Current UTC time: {cur_time}
    Task Owner Name: {owner_name}

    {format_instructions}
    """

    format_instructions = parser_lc.get_format_instructions()
    prompt = PromptTemplate(
        template=prompt_template,
        input_variables=["content_message", "cur_time", "sender_info"],
        partial_variables={"format_instructions": format_instructions}
    )

    # Initialize the language model
    llm = ChatOpenAI(
        model="gpt-4",
        temperature=0,
        max_tokens=None,
        openai_api_key=OPENAI_API_KEY
    )

    _input_kwargs = {
        "content_message": content_message,
        "cur_time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "owner_name": owner_name,
        "location": location
    }

    # Format the prompt using the provided inputs
    _input = prompt.format(**_input_kwargs)
    logger.debug(f"LLM Input: {_input}")

    try:
        # Call the language model to get the output
        output = llm([HumanMessage(content=_input)])
        logger.debug(f"LLM Output: {output.content}")
        # Parse the output into the expected format
        parsed_task = parser_lc.parse(output.content)
        logger.debug(f"Parsed task: {parsed_task}")
        return parsed_task.dict()
    except Exception as e:
        logger.error(f"OpenAI API error: {e}")
        return None

__all__ = ['parse_description_with_langchain']