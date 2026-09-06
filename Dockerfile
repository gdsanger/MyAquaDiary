FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements/ requirements/
ARG REQUIREMENTS_FILE=requirements/development.txt
RUN pip install --no-cache-dir -r ${REQUIREMENTS_FILE}

COPY . .

EXPOSE 8000

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
