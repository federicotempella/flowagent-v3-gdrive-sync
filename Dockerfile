# Base Python slim
FROM python:3.12-slim

# Evita interazioni durante apt-get
ENV DEBIAN_FRONTEND=noninteractive

# Aggiorna e installa i binari necessari a OCR
# - tesseract-ocr: OCR engine
# - tesseract-ocr-<lang>: dati lingua (ENG, FRA, SPA, NLD, IT)
# - poppler-utils: per "pdf2image" (pdf -> immagini)
# - libgl1: dipendenza di Pillow su alcune architetture
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-fra \
    tesseract-ocr-spa \
    tesseract-ocr-nld \
    tesseract-ocr-ita \
    poppler-utils \
    libgl1 \
 && rm -rf /var/lib/apt/lists/*

# (Opzionale) imposta lingua predefinita OCR se vuoi "eng" come fallback
ENV TESSERACT_LANG=eng+fra+spa+nld+ita

# Crea directory app
WORKDIR /app

# Copia requisiti e installa dipendenze Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia il resto del codice
COPY . .

# Porta su cui ascolta Flask
ENV PORT=10000
EXPOSE 10000

# Avvio app
CMD ["python", "app.py"]
