FROM python:3.12-slim

# Keeps Python from buffering stdout/stderr
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py ui.html ./

EXPOSE 5006

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5006"]
