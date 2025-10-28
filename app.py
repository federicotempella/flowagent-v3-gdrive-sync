import os, io, time, json, threading, requests
from datetime import datetime
from flask import Flask
from google.oauth2 import service_account
from googleapiclient.discovery import build

# === CONFIG ===
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
FOLDER_ID = os.getenv("GOOGLE_FOLDER_ID")
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "1800"))
CREDENTIALS_INFO = json.loads(os.getenv("GOOGLE_CREDENTIALS"))

SCOPES = ["https://www.googleapis.com/auth/drive.metadata.readonly"]
creds = service_account.Credentials.from_service_account_info(CREDENTIALS_INFO, scopes=SCOPES)
drive = build("drive", "v3", credentials=creds)
GPT_ENDPOINT = "https://api.openai.com/v1/chat/completions"

app = Flask(__name__)
_seen = {}

def list_files(folder_id):
    query = f"'{folder_id}' in parents and trashed=false"
    results = drive.files().list(q=query, fields="files(id,name,modifiedTime,mimeType)").execute()
    files = results.get("files", [])
    for f in list(files):
        if f["mimeType"] == "application/vnd.google-apps.folder":
            files.extend(list_files(f["id"]))
    return files

def notify_gpt(fileinfo):
    name = fileinfo["name"]
    mod = fileinfo["modifiedTime"]
    msg = f"[Flowagent V3] Updated file: {name} (lastModified={mod})"
    print(msg)
    headers = {"Authorization": f"Bearer {OPENAI_KEY}"}
    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role":"system","content":"Flowagent V3 repository update – refresh knowledge if needed."},
            {"role":"user","content": msg}
        ]
    }
    try:
        requests.post(GPT_ENDPOINT, headers=headers, json=payload, timeout=30)
    except Exception as e:
        print("Notify error:", e)

def poll_loop():
    while True:
        try:
            files = list_files(FOLDER_ID)
            files.sort(key=lambda x: x["modifiedTime"], reverse=True)
            for f in files[:50]:
                fid, mod = f["id"], f["modifiedTime"]
                if _seen.get(fid) != mod:
                    _seen[fid] = mod
                    notify_gpt(f)
        except Exception as e:
            print("Poll error:", e)
        time.sleep(POLL_SECONDS)

@app.route("/")
def ok():
    return "Flowagent V3 Google Drive Sync active"

def start_background():
    t = threading.Thread(target=poll_loop, daemon=True)
    t.start()

if __name__ == "__main__":
    start_background()
    app.run(host="0.0.0.0", port=10000)
