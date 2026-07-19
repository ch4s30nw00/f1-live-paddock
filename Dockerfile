FROM python:3.13-slim

WORKDIR /app

# 의존성 레이어를 분리해 코드만 바뀌면 pip install을 다시 하지 않는다
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
