from __future__ import annotations
import os
import logging
from langchain_mistralai import ChatMistralAI
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

def get_llm() -> ChatMistralAI:
    return ChatMistralAI(
        model='devstral-small-latest',
        api_key=os.getenv("MISTRAL_API_KEY2"),
        temperature=0.0,
        max_tokens=2048,
    )

def get_llm_DB() -> ChatMistralAI:
    return ChatMistralAI(
        model='devstral-small-latest',
        api_key=os.getenv("MISTRAL_API_KEY2"),
        temperature=0.0,
        max_tokens=1024,
    )