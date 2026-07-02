FROM python:3.11-slim
WORKDIR /imap
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY imap.py .
CMD ["python", "imap.py"]