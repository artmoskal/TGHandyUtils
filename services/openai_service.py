from openai import AsyncOpenAI
import os

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

async def transcribe_audio(audio_data):
    """Handle OpenAI transcription with file data directly"""
    if not OPENAI_API_KEY:
        raise ValueError("Please set OPENAI_API_KEY in the .env file.")
    
    client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    response = await client.audio.transcriptions.create(
        model="whisper-1",
        file=audio_data
    )
    return response.text 