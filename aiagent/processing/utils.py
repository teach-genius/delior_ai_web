import itertools
from langchain_nvidia_ai_endpoints import ChatNVIDIA
from langchain_mistralai import ChatMistralAI
from dotenv import load_dotenv
import os

load_dotenv()

# API_KEYS    = os.getenv("NVIDIA_API_KEYS", "").split(",")
# max_retries = len(API_KEYS)
# _key_cycle  = itertools.cycle(API_KEYS)

# def get_next_llm():
#     key = next(_key_cycle)
#     return ChatNVIDIA(
#         model="qwen/qwen2.5-coder-32b-instruct",
#         api_key=key,
#         temperature=0.0,
#         max_tokens=2048,
#     )
max_retries = 3

_llm = ChatMistralAI(
    model="mistral-small-latest",
    api_key=os.getenv("MISTRAL_API_KEY"),
    temperature=0.0
)

def get_next_llm():
    return _llm