from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CACHE_FILE = BASE_DIR / ".token_cache.json"

CLIENT_ID   = "365c1a7c-0b5f-44a5-a816-d49e9c52b24f"
TENANT_ID   = "55ca6f41-b16f-4578-99f3-75b7177ea1ad"

AUTHORITY   = f"https://login.microsoftonline.com/{TENANT_ID}"
SCOPES      = ["https://graph.microsoft.com/Mail.Read.Shared"]
GRAPH_BASE  = "https://graph.microsoft.com/v1.0"

