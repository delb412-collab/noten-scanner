# Noten-Scanner Server für Hugging Face Spaces (Docker, CPU, kostenlose Stufe)
FROM python:3.13-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends git libcairo2 libglib2.0-0 libgl1 \
 && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 user
WORKDIR /home/user/app

COPY requirements.txt .
# Modelle der Notenerkennung (ca. 150 MB) schon beim Bauen laden, damit der Start schnell geht
RUN pip install --no-cache-dir -r requirements.txt && homr --init \
 && python -c "from rapidocr import RapidOCR; RapidOCR()"
# (die Texterkennung für Pausenzahlen lädt ihre Modelle sonst erst zur Laufzeit – dann ohne Schreibrecht)

COPY --chown=user . .
USER user
ENV HOME=/home/user PYTHONUNBUFFERED=1 OMP_NUM_THREADS=2
EXPOSE 7860
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7860"]
