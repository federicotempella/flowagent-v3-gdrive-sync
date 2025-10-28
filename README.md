🧠 Flowagent V3 – Google Drive Repository Sync (Render Deploy Guide)

Questo servizio sincronizza automaticamente la cartella Google Drive che contiene le risorse interne di Flowagent V3 (CORE, KNOWLEDGE, TONE) e le espone via API per l’Action Flowagent Repository del GPT.

🚀 1️⃣ Prerequisiti
Requisito	Descrizione
Google Cloud Project	Creato e con Service Account abilitato all’API Drive v3.
File di credenziali	JSON del Service Account (ruolo: Drive Viewer o Drive Reader).
Cartella Google Drive	Tutti i materiali (Frameworks, Case Studies, Tone, New Materials, ecc.). Copia l’ID dall’URL.
Account Render	Anche il piano Free è sufficiente.
GitHub Repo	Il codice del progetto (con app.py, requirements.txt, Dockerfile, render.yaml, ecc.).
⚙️ 2️⃣ Configurazione su Render
2.1 Collega GitHub

Vai su https://render.com
 → New → Web Service.

Seleziona il repository Flowagent V3 Sync.

Branch: main · Region: Frankfurt · Environment: Docker.

2.2 Variabili di Ambiente

Apri la scheda Environment → Add Environment Variable e incolla le seguenti chiavi (valori reali dove richiesto):

Chiave	Descrizione / Esempio
OPENAI_API_KEY	chiave OpenAI (se usi notify_openai)
GOOGLE_FOLDER_ID	ID cartella Drive es: 1gb5DkAqrhnYsULimZOvJjSauTtbXbbYB
GOOGLE_CREDENTIALS	JSON service account su una sola riga con \n
BEARER_TOKEN	token segreto GPT (es. mio_token_supersegreto)
POLL_SECONDS	1800 (30 minuti default)
OCR_ENABLED	1 per abilitare OCR
TESSERACT_LANG	eng+fra+spa+nld+ita
PORT	10000 (default)
🐳 3️⃣ Dockerfile di deploy (già incluso)

Il progetto usa un’immagine Python 3.12 slim con i binari OCR multilingua:

FROM python:3.12-slim
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr tesseract-ocr-eng tesseract-ocr-fra \
    tesseract-ocr-spa tesseract-ocr-nld tesseract-ocr-ita \
    poppler-utils libgl1 && rm -rf /var/lib/apt/lists/*
ENV TESSERACT_LANG=eng+fra+spa+nld+ita
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PORT=10000
EXPOSE 10000
CMD ["python", "app.py"]

🧩 4️⃣ render.yaml
services:
  - type: web
    name: flowagent-v3-gdrive-sync
    plan: free
    region: frankfurt
    env: docker
    dockerfilePath: ./Dockerfile
    autoDeploy: true
    healthCheckPath: /
    envVars:
      - key: OPENAI_API_KEY
        sync: false
      - key: GOOGLE_FOLDER_ID
        sync: false
      - key: GOOGLE_CREDENTIALS
        sync: false
      - key: BEARER_TOKEN
        sync: false
      - key: OCR_ENABLED
        value: "1"
      - key: TESSERACT_LANG
        value: "eng+fra+spa+nld+ita"

🔁 5️⃣ Cronjob Keep-Alive (opzionale)

Render Free sospende i container inattivi dopo ~15 minuti.
Crea un ping su https://cron-job.org
:

URL → https://<appname>.onrender.com/read?id=ping

Frequence → ogni 10 minuti

Attivo 24 h

🧠 6️⃣ Verifica nei log

Apri la sezione Logs su Render: al boot dovresti vedere

=== Flowagent V3 Repository Service ===
OCR_ENABLED = True
TESSERACT_LANG = eng+fra+spa+nld+ita
GOOGLE_FOLDER_ID = 1gb5DkAqrhnYsULimZOvJjSauTtbXbbYB
POLL_SECONDS = 1800
=======================================
[SYNC] Inizio sincronizzazione alle ...
[SYNC] Repository aggiornato alle ... — 2 file nuovi/aggiornati

🔒 7️⃣ Integrazione con GPT

Nel GPT Builder → Actions → Add from schema, importa lo schema OpenAPI del servizio (/openapi.json se previsto).
Imposta l’autenticazione Bearer con il token mio_token_supersegreto.

Il GPT potrà chiamare /search e /read per accedere in tempo reale alle fonti interne.

🧾 8️⃣ Troubleshooting
Problema	Soluzione
OCR non disponibile	assicurati che OCR_ENABLED=1 e che il Dockerfile includa tesseract/poppler
401 Unauthorized	verifica che il token Bearer nel GPT coincida con quello su Render
Polling non sincronizza	controlla che GOOGLE_CREDENTIALS siano validi e che il Service Account abbia accesso alla cartella
App si ferma	aggiungi cronjob keep-alive ogni 10 minuti
✅ 9️⃣ Test rapido

Apri nel browser:

https://<appname>.onrender.com/search?query=buyer%20persona


Dovresti ricevere un JSON con i risultati dei file interni.

Verifica anche:

https://<appname>.onrender.com/read?id=<IDfile>


per assicurarti che l’estrazione testo funzioni.

🧩 10️⃣ Checklist prima del deploy

 Dockerfile presente e corretto

 render.yaml aggiornato

 Variabili Render configurate

 OCR_ENABLED=1 e TESSERACT_LANG=eng+fra+spa+nld+ita

 BEARER_TOKEN uguale nel GPT Action

 /read e /search testati

💡 Suggerimento finale: se vuoi debug più silenzioso, puoi impostare LOG_VERBOSE=0 e nel codice spegnere i print di sync con un semplice check.
