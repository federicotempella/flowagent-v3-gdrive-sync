import os, io, time, json, threading, requests, re
from datetime import datetime
from flask import Flask, request, jsonify, Response, send_from_directory
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import base64
from googleapiclient.http import MediaIoBaseUpload

# OCR optional deps (won't crash if missing)
try:
    import pytesseract
    from pdf2image import convert_from_bytes
    from PIL import Image
    _ocr_available = True
except Exception:
    _ocr_available = False
def load_service_account_info():
    raw = os.getenv("GOOGLE_CREDENTIALS", "")
    if not raw:
        raise RuntimeError("Missing GOOGLE_CREDENTIALS env")
    raw = raw.strip()

    # Se è JSON "in chiaro"
    if raw.startswith("{"):
        info = json.loads(raw)
    else:
        # altrimenti assume base64
        info = json.loads(base64.b64decode(raw).decode("utf-8"))

    # Normalizza la private_key: trasforma \\n in newline reali
    pk = info.get("private_key", "")
    if "\\n" in pk:
        info["private_key"] = pk.replace("\\n", "\n")
    return info

# === ENV ===
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
FOLDER_ID = os.getenv("GOOGLE_FOLDER_ID")
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "1800"))
CREDENTIALS_INFO = load_service_account_info()   # <--- usa la funzione
BEARER_TOKEN = os.getenv("BEARER_TOKEN")
OCR_ENABLED = os.getenv("OCR_ENABLED", "0") == "1"
lang = os.getenv("TESSERACT_LANG", "eng")
LOG_VERBOSE = os.getenv("LOG_VERBOSE", "1") == "1"

def vlog(msg: str):
    if LOG_VERBOSE:
        print(msg)

# Google Drive client (metadata read)
SCOPES = ["https://www.googleapis.com/auth/drive"]
creds = service_account.Credentials.from_service_account_info(CREDENTIALS_INFO, scopes=SCOPES)
drive = build("drive", "v3", credentials=creds)

GPT_ENDPOINT = "https://api.openai.com/v1/chat/completions"

app = Flask(__name__)

# In-memory caches
_index = {}   # fileId -> {id,name,path,mimeType,modifiedTime}
_recent = []  # lista degli ultimi file modificati (per /updates)


def ensure_index_ready():
    # build initial index if empty
    global _index
    if not _index:
        try:
            new_index = build_index()
            # publish
            for meta in new_index.values():
                _recent.append(meta)
            refresh_recent(new_index)
            _index = new_index
        except Exception as e:
            print("Initial index error:", e)


# ---------- Utils ----------
def bearer_ok(req):
    auth = req.headers.get("Authorization", "")
    if not (BEARER_TOKEN and auth.startswith("Bearer ") and auth.split(" ", 1)[1] == BEARER_TOKEN):
        vlog("[WARN] Unauthorized request or missing BEARER_TOKEN")
        return False
    return True

def list_children(folder_id):
    # lista immediata dei figli
    q = f"'{folder_id}' in parents and trashed=false"
    fields = "files(id,name,mimeType,modifiedTime)"
    res = drive.files().list(q=q, fields=fields, pageSize=200).execute()
    return res.get("files", [])

def build_index():
    # ricorsivo su cartelle
    stack = [(FOLDER_ID, "")]
    new_index = {}
    while stack:
        parent_id, prefix = stack.pop()
        for item in list_children(parent_id):
            fid = item["id"]
            name = item["name"]
            mime = item["mimeType"]
            path = f"{prefix}/{name}".lstrip("/")
            mod  = item.get("modifiedTime", "")
            new_index[fid] = {"id": fid, "name": name, "mimeType": mime, "path": path, "modifiedTime": mod}
            if mime == "application/vnd.google-apps.folder":
                stack.append((fid, path))
    return new_index

def refresh_recent(new_index):
    global _recent
    # prendi i top 100 per data modifica
    vals = list(new_index.values())
    vals.sort(key=lambda x: x.get("modifiedTime",""), reverse=True)
    _recent = vals[:100]

def notify_openai(fileinfo):
    if not OPENAI_KEY:  # opzionale
        return
    msg = f"[Flowagent V3] Updated file: {fileinfo['path']} (lastModified={fileinfo['modifiedTime']})"
    try:
        requests.post(
            GPT_ENDPOINT,
            headers={"Authorization": f"Bearer {OPENAI_KEY}"},
            json={
                "model": "gpt-4o-mini",
                "messages": [
                    {"role":"system","content":"Flowagent V3 repository update – refresh knowledge if needed."},
                    {"role":"user","content": msg}
                ]
            },
            timeout=20
        )
    except Exception as e:
        print("OpenAI notify error:", e)

def poll_loop():
    """Loop di sincronizzazione periodica del repository Google Drive."""
    global _index
    while True:
        try:
            vlog(f"[SYNC] Inizio sincronizzazione alle {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            new_index = build_index()

            updated_files = []
            for fid, meta in new_index.items():
                if (fid not in _index) or (_index[fid].get("modifiedTime") != meta.get("modifiedTime")):
                    notify_openai(meta)
                    updated_files.append({
                        "name": meta.get("name"),
                        "mimeType": meta.get("mimeType"),
                        "modifiedTime": meta.get("modifiedTime")
                    })

            _index = new_index
            refresh_recent(_index)

            if updated_files:
                vlog(f"[SYNC] Repository aggiornato alle {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} — {len(updated_files)} file nuovi/aggiornati:")
                for f in updated_files:
                    vlog(f"   • {f['name']} ({f['mimeType']}) — modificato {f['modifiedTime']}")
            else:
                vlog(f"[SYNC] Nessun file nuovo/aggiornato alle {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        except Exception as e:
            # gli errori li lascio SEMPRE a video, anche se LOG_VERBOSE=0
            print(f"[ERROR] Polling fallito alle {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}: {e}")

        time.sleep(POLL_SECONDS)   # <-- tolta la parentesi extra


# ---------- Readers ----------
def read_google_doc_text(file_id):
    # export Google Docs/Sheets/Slides to text/plain when possible
    try:
        req = drive.files().export_media(fileId=file_id, mimeType="text/plain")
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, req)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buf.getvalue().decode("utf-8", errors="ignore")
    except Exception as e:
        raise

def download_file_bytes(file_id):
    req = drive.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, req)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue()

def extract_text_from_bytes(mime, data):
    # 1) percorsi testuali "normali"
    if mime in ("text/plain", "application/json", "application/xml"):
        try:
            return data.decode("utf-8", errors="ignore")
        except Exception:
            return data.decode("latin-1", errors="ignore")

    if mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        try:
            from docx import Document
            bio = io.BytesIO(data)
            doc = Document(bio)
            return "\n".join([p.text for p in doc.paragraphs])
        except Exception:
            return ""

    if mime == "application/pdf":
        # prima tentiamo estrazione testo nativa
        try:
            from pdfminer.high_level import extract_text
            bio = io.BytesIO(data)
            t = extract_text(bio) or ""
        except Exception:
            t = ""
        if t.strip():
            return t
        # fallback OCR se abilitato e possibile
        if OCR_ENABLED:
            t = ocr_pdf_bytes(data)
            if t.strip():
                return t
        return ""

    # immagini: prova OCR
    if mime in ("image/jpeg", "image/png", "image/tiff"):
        if OCR_ENABLED:
            t = ocr_image_bytes(data)
            if t.strip():
                return t
        return ""

    # fallback: non gestibile
    return ""


def ocr_images(images):
    """OCR su una lista di immagini PIL -> testo concatenato"""
    lang = os.getenv("TESSERACT_LANG", "eng")
    text_chunks = []
    for img in images:
        try:
            text = pytesseract.image_to_string(img, lang=lang)
            if text:
                text_chunks.append(text)
        except Exception:
            pass
    return "\n".join(t for t in text_chunks if t and t.strip())

def ocr_pdf_bytes(pdf_bytes):
    """Converte PDF in immagini e fa OCR; richiede poppler + tesseract nel sistema."""
    if not _ocr_available:
        return ""
    try:
        images = convert_from_bytes(pdf_bytes, dpi=200)  # può richiedere poppler
        return ocr_images(images)
    except Exception:
        return ""

def ocr_image_bytes(img_bytes):
    """OCR su un’immagine singola (jpeg/png)."""
    if not _ocr_available:
        return ""
    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        return pytesseract.image_to_string(img, lang=os.getenv("TESSERACT_LANG", "eng"))
    except Exception:
        return ""


# ---------- Routes ----------
@app.route("/")
def health():
    return "Flowagent V3 Repository API active"

@app.route("/updates")
def updates():
    if not bearer_ok(request):
        return jsonify({"error":"unauthorized"}), 401
    return jsonify({"files": _recent})

@app.route("/search")
def search():
    if not bearer_ok(request):
        return jsonify({"error":"unauthorized"}), 401
    ensure_index_ready()
    q = (request.args.get("q") or "").strip()
    limit = max(1, min(int(request.args.get("limit", "10")), 50))
    if not q:
        return jsonify({"files": _recent[:limit]})
    pattern = re.compile(re.escape(q), re.IGNORECASE)
    hits = [m for m in _index.values() if pattern.search(m["name"]) or pattern.search(m["path"])]
    # ordina per recency
    hits.sort(key=lambda x: x.get("modifiedTime",""), reverse=True)
    return jsonify({"files": hits[:limit]})

@app.route("/write", methods=["POST"])
def write():
    if not bearer_ok(request):
        return jsonify({"error": "unauthorized"}), 401

    payload = request.get_json(force=True)
    rel_path = payload.get("path", "").strip()
    content = payload.get("content", None)
    overwrite = payload.get("overwrite", True)

    if not rel_path or content is None:
        return jsonify({"error": "missing path or content"}), 400

    # ✅ Se content è una stringa (serializzata da GPT), prova a parsarlo
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except Exception:
            return jsonify({"error": "Invalid JSON string in content"}), 400

    try:
        filename = os.path.basename(rel_path)
        media_body = MediaIoBaseUpload(
            io.BytesIO(json.dumps(content, indent=2, ensure_ascii=False).encode("utf-8")),
            mimetype="application/json"
        )

        # Controllo se esiste già
        query = f"name = '{filename}' and trashed = false and '{FOLDER_ID}' in parents"
        existing = drive.files().list(q=query, spaces="drive", fields="files(id, name)").execute().get("files", [])

        if existing and not overwrite:
            return jsonify({"error": "file already exists and overwrite=false"}), 409

        if existing:
            file_id = existing[0]["id"]
            drive.files().update(fileId=file_id, media_body=media_body).execute()
        else:
            file_metadata = {
                "name": filename,
                "parents": [FOLDER_ID],
                "mimeType": "application/json"
            }
            drive.files().create(body=file_metadata, media_body=media_body).execute()

        return jsonify({"status": "success", "message": f"File salvato: {rel_path}"}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/read")
def read():
    if not bearer_ok(request):
        return jsonify({"error": "unauthorized"}), 401

    ensure_index_ready()
    fid = request.args.get("id")
    if not fid or fid not in _index:
        return jsonify({"error": "missing_or_unknown_id"}), 400

    meta = _index[fid]
    mime = meta["mimeType"]

    # Google Workspace file → export to text (es. Google Docs)
    if mime.startswith("application/vnd.google-apps."):
        try:
            text = read_google_doc_text(fid)
            if text and text.strip():
                return Response(text, mimetype="text/plain; charset=utf-8")
            # Se export ok ma vuoto/illeggibile:
            return jsonify({
                "id": fid, "name": meta["name"], "mimeType": mime,
                "message": "Export to text returned empty content. Consider converting the document or pasting plain text."
            }), 200
        except Exception:
            return jsonify({"error": "export_failed"}), 500

    # File binari (pdf, docx, txt, immagini, ecc.)
    try:
        data = download_file_bytes(fid)
    except Exception:
        return jsonify({"error": "download_failed"}), 500

    # 1) Tentativo di estrazione testo 'normale'
    text = extract_text_from_bytes(mime, data)
    if text.strip():
        return Response(text, mimetype="text/plain; charset=utf-8")

    # 2) Se non estraibile, verifica se avrebbe senso l'OCR
    needs_ocr = mime in ("application/pdf", "image/jpeg", "image/png", "image/tiff")

    # Se l’OCR è abilitato via ENV ma le dipendenze non ci sono:
    if needs_ocr and OCR_ENABLED and not _ocr_available:
        return jsonify({
            "id": fid, "name": meta["name"], "mimeType": mime,
            "message": "Unable to extract text automatically. OCR may be required but is not available in this environment. "
                       "Enable OCR (set OCR_ENABLED=1) with Tesseract+Poppler in the image, or convert to Google Docs/TXT."
        }), 200

    # Se l’OCR non è abilitato:
    if needs_ocr and not OCR_ENABLED:
        return jsonify({
            "id": fid, "name": meta["name"], "mimeType": mime,
            "message": "Unable to extract text automatically. This file likely requires OCR. "
                       "Enable OCR (set OCR_ENABLED=1) and deploy with Tesseract+Poppler, or convert to Google Docs/TXT."
        }), 200

    # Caso generale: non testuale / non gestibile
    return jsonify({
        "id": fid, "name": meta["name"], "mimeType": mime,
        "message": "Unable to extract text; consider converting to Google Docs or uploading TXT."
    }), 200


def start_background():
    # Log di stato ambiente OCR e lingua (utile per controllare Render)
    vlog("=== Flowagent V3 Repository Service ===")
    vlog(f"OCR_ENABLED = {OCR_ENABLED}")
    vlog(f"TESSERACT_LANG = {lang}")
    vlog(f"GOOGLE_FOLDER_ID = {FOLDER_ID}")
    vlog(f"POLL_SECONDS = {POLL_SECONDS}")
    vlog("=======================================")

    # Avvio thread di polling repository
    t = threading.Thread(target=poll_loop, daemon=True)
    t.start()
    
@app.route("/.well-known/openapi.yaml")
def serve_openapi_spec():
    return send_from_directory(".well-known", "openapi.yaml", mimetype="application/yaml")

@app.route("/.well-known/ai-plugin.json")
def serve_plugin_manifest():
    """
    Serve il manifest del plugin per ChatGPT.
    Deve essere accessibile via: /.well-known/ai-plugin.json
    """
    return send_from_directory(".well-known", "ai-plugin.json", mimetype="application/json")

@app.route("/.well-known/logo.png")
def serve_logo():
    return send_from_directory(".well-known", "logo.png", mimetype="image/png")

@app.route("/healthz", methods=["GET"])
def healthz():
    """
    Lightweight health check endpoint.
    Returns 200 OK if the service is alive (no sync triggered).
    """
    return jsonify({
        "status": "ok",
        "timestamp": datetime.now().astimezone().isoformat()
    }), 200

if __name__ == "__main__":
    start_background()
    app.run(host="0.0.0.0", port=10000)

































