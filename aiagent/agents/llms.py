from __future__ import annotations
import os
import logging
from langchain_mistralai import ChatMistralAI
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


def get_llm() -> ChatMistralAI:
    return ChatMistralAI(
        model='devstral-small-latest',#"mistral-small-latest",
        api_key=os.getenv("MISTRAL_API_KEY2"),
        temperature=0.0,
        max_tokens=2048,
    )


def get_llm_DB() -> ChatMistralAI:
    return ChatMistralAI(
        model='devstral-small-latest',#"mistral-small-latest",
        api_key=os.getenv("MISTRAL_API_KEY2"),
        temperature=0.0,
        max_tokens=1024,
    )

# _BASE_URL = "http://localhost:8002/v1"
# _API_KEY  = os.getenv("CHAT_SECRET_KEY", "no-key")
# _MODEL    = "Qwen2.5-1.5B-Instruct.Q4_K_M.gguf"

# def _make_llm(max_tokens: int = 512, temperature: float = 0.0) -> ChatOpenAI:
#     return ChatOpenAI(
#         model       = _MODEL,
#         base_url    = _BASE_URL,
#         api_key     = _API_KEY,
#         temperature = temperature,
#         max_tokens  = max_tokens,
#     )