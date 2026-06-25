import requests
from aiagent.auths.config import GRAPH_BASE,SHARED_MAILBOX

def _headers(token):
    return {"Authorization": f"Bearer {token}"}

def get_inbox(token, top=20):
    resp = requests.get(
        f"{GRAPH_BASE}/me/mailFolders/inbox/messages",
        headers=_headers(token),
        params={
            "$top": top,
            "$select": "subject,from,receivedDateTime,isRead,bodyPreview",
            "$orderby": "receivedDateTime desc"
        }
    )
    resp.raise_for_status()
    return resp.json().get("value", [])

def get_message_body(token, message_id):
    resp = requests.get(
        f"{GRAPH_BASE}/me/messages/{message_id}",
        headers=_headers(token),
        params={"$select": "subject,body,from,toRecipients,receivedDateTime"}
    )
    resp.raise_for_status()
    return resp.json()

def get_unread(token, top=10):
    resp = requests.get(
        f"{GRAPH_BASE}/me/mailFolders/inbox/messages",
        headers=_headers(token),
        params={
            "$top": top,
            "$filter": "isRead eq false",
            "$select": "subject,from,receivedDateTime,bodyPreview",
            "$orderby": "receivedDateTime desc"
        }
    )
    resp.raise_for_status()
    return resp.json().get("value", [])

def get_delta(token, delta_token=None):
    params = {"$select": "subject,from,receivedDateTime,isRead"}
    if delta_token:
        params["$deltatoken"] = delta_token
    resp = requests.get(
        f"{GRAPH_BASE}/me/mailFolders/inbox/messages/delta",
        headers=_headers(token),
        params=params
    )
    resp.raise_for_status()
    data      = resp.json()
    messages  = data.get("value", [])
    next_delta = data.get("@odata.deltaLink", "").split("$deltatoken=")[-1] or None
    return messages, next_delta

def get_shared_inbox(token, top=20):
    resp = requests.get(
        f"{GRAPH_BASE}/users/{SHARED_MAILBOX}/mailFolders/inbox/messages",
        headers=_headers(token),
        params={
            "$top": top,
            "$select": "subject,from,receivedDateTime,isRead,bodyPreview",
            "$orderby": "receivedDateTime desc"
        }
    )
    if not resp.ok:
        print(f"  status : {resp.status_code}")
        print(f"  error  : {resp.json()}")
    resp.raise_for_status()
    return resp.json().get("value", [])


def get_shared_unread(token, top=10):
    """Fetch unread messages from the shared mailbox."""
    resp = requests.get(
        f"{GRAPH_BASE}/users/{SHARED_MAILBOX}/mailFolders/inbox/messages",
        headers=_headers(token),
        params={
            "$top": top,
            "$filter": "isRead eq false",
            "$select": "subject,from,receivedDateTime,bodyPreview",
            "$orderby": "receivedDateTime desc"
        }
    )
    resp.raise_for_status()
    return resp.json().get("value", [])