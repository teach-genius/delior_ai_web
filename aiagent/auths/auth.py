import os
import json
import msal
from datetime import datetime, timezone
from aiagent.auths.config import CLIENT_ID, AUTHORITY, SCOPES, CACHE_FILE

def _load_cache():
    cache = msal.SerializableTokenCache()
    if os.path.exists(CACHE_FILE):
        cache.deserialize(open(CACHE_FILE).read())
    return cache

def _save_cache(cache):
    if cache.has_state_changed:
        with open(CACHE_FILE, "w") as f:
            f.write(cache.serialize())

def check_token_health():
    """Warn if refresh token is close to 90-day expiry."""
    if not os.path.exists(CACHE_FILE):
        return
    try:
        cache_data = json.loads(open(CACHE_FILE).read())
        rt_entries = cache_data.get("RefreshToken", {})
        for _, rt in rt_entries.items():
            last_modified = rt.get("last_modification_time")
            if last_modified:
                last_used     = datetime.fromtimestamp(float(last_modified), tz=timezone.utc)
                days_since    = (datetime.now(timezone.utc) - last_used).days
                days_remaining = 90 - days_since
                if days_remaining <= 14:
                    print(f"  ⚠  Token expires in {days_remaining} days — "
                          f"delete {CACHE_FILE} and re-run to refresh.")
    except Exception:
        pass  # cache unreadable — will re-auth automatically

def get_token():
    check_token_health()

    cache = _load_cache()
    app   = msal.PublicClientApplication(
        client_id=CLIENT_ID,
        authority=AUTHORITY,
        token_cache=cache
    )

    accounts = app.get_accounts()

    # Silent refresh — uses cached token, no browser needed
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])
        if result and "access_token" in result:
            _save_cache(cache)
            return result["access_token"]

    # First run or cache expired — device code flow
    flow = app.initiate_device_flow(scopes=SCOPES)
    print("\n" + "─" * 55)
    print(flow["message"])
    print("─" * 55)
    print("\nOpen the URL above on any device (laptop, phone).")
    input("Press Enter here after you have signed in...")

    result = app.acquire_token_by_device_flow(flow)
    _save_cache(cache)

    if "access_token" not in result:
        raise Exception(f"Auth failed: {result.get('error_description')}")

    return result["access_token"]

if __name__ == '__main__':
    print('start')	
    get_token()
