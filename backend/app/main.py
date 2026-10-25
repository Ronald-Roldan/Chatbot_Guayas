from __future__ import annotations

import logging
import os
import re
import tempfile
import time
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

import fitz
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from google import genai
from google.genai import errors, types
from groq import Groq
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from supabase import Client, create_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    supabase_url: str
    supabase_service_role_key: str
    google_api_key: str
    groq_api_key: str
    google_embedding_model: str = "gemini-embedding-001"
    embedding_dimensions: int = 768
    groq_model: str = "openai/gpt-oss-20b"
    max_upload_mb: int = Field(default=0, ge=0)
    top_k: int = 5
    match_threshold: float = 0.25
    allowed_origins: str = "*"


settings = Settings()
supabase: Client = create_client(settings.supabase_url, settings.supabase_service_role_key)
genai_client = genai.Client(api_key=settings.google_api_key)
groq_client = Groq(api_key=settings.groq_api_key, base_url="https://api.groq.com")

app = FastAPI(title="RAG API", version="1.0.0")
upload_jobs: dict[str, dict[str, Any]] = {}
upload_jobs_lock = Lock()
origins = [origin.strip() for origin in settings.allowed_origins.split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if "*" in origins else origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=150)


class ChatRequest(BaseModel):
    question: str = Field(min_length=2, max_length=4000)


def embed(text: str, task_type: str) -> list[float]:
    return embed_many([text], task_type)[0]


def embed_many(texts: list[str], task_type: str) -> list[list[float]]:
    for attempt in range(4):
        try:
            result = genai_client.models.embed_content(
                model=settings.google_embedding_model,
                contents=texts,
                config=types.EmbedContentConfig(
                    task_type=task_type,
                    output_dimensionality=settings.embedding_dimensions,
                ),
            )
            if not result.embeddings or len(result.embeddings) != len(texts):
                raise RuntimeError("Google devolvió una cantidad incorrecta de embeddings")
            if any(not embedding.values for embedding in result.embeddings):
                raise RuntimeError("Google devolvió un embedding vacío")
            return [list(embedding.values) for embedding in result.embeddings]
        except errors.APIError as exc:
            if exc.code != 429:
                raise
            if attempt == 3:
                raise HTTPException(
                    status_code=503,
                    detail="Se agotó temporalmente la cuota de embeddings de Google. Espera un minuto y vuelve a intentarlo.",
                ) from exc
            retry_after = re.search(r"retry in ([\d.]+)s", str(exc), re.IGNORECASE)
            delay = float(retry_after.group(1)) if retry_after else min(60, 5 * (2**attempt))
            delay = min(60, max(1, delay + 1))
            logger.warning("Cuota de embeddings agotada; reintentando en %.0f segundos", delay)
            time.sleep(delay)


@app.get("/")
def root() -> dict[str, str]:
    return {"name": "RAG API", "status": "ok", "docs": "/docs"}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def update_upload_job(job_id: str, **updates: Any) -> None:
    with upload_jobs_lock:
        upload_jobs[job_id].update(updates)


def process_pdf(temp_path: str, filename: str, job_id: str) -> None:
    try:
        with fitz.open(temp_path) as pdf:
            pages = [page.get_text("text") for page in pdf]
        full_text = "\n".join(pages).strip()
        if not full_text:
            raise HTTPException(status_code=422, detail="No se encontró texto. El PDF podría ser un escaneo sin OCR.")

        chunks = splitter.split_text(full_text)
        update_upload_job(job_id, status="processing", pages=len(pages), total_chunks=len(chunks))
        embeddings = []
        for offset in range(0, len(chunks), 50):
            batch = chunks[offset : offset + 50]
            embeddings.extend(embed_many(batch, "RETRIEVAL_DOCUMENT"))
            update_upload_job(job_id, processed_chunks=len(embeddings))
            logger.info("Trabajo %s: embeddings para %s de %s fragmentos", job_id, len(embeddings), len(chunks))

        rows = [
            {
                "content": chunk,
                "embedding": embeddings[index],
                "metadata": {"source": filename, "chunk": index, "pages": len(pages)},
            }
            for index, chunk in enumerate(chunks)
        ]
        for offset in range(0, len(rows), 50):
            supabase.table("documents").insert(rows[offset : offset + 50]).execute()

        result = {
            "filename": filename,
            "pages": len(pages),
            "chunks": len(chunks),
            "message": "PDF procesado",
        }
        update_upload_job(job_id, status="completed", result=result)
    except HTTPException as exc:
        update_upload_job(job_id, status="failed", error=exc.detail)
    except Exception:
        logger.exception("Falló el procesamiento del PDF, trabajo %s", job_id)
        update_upload_job(
            job_id,
            status="failed",
            error="No se pudo procesar el PDF. Revisa las claves y los logs del backend.",
        )
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


@app.post("/upload", status_code=202)
def upload_pdf(background_tasks: BackgroundTasks, file: UploadFile = File(...)) -> dict[str, Any]:
    if not file.filename or Path(file.filename).suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="Solo se aceptan archivos PDF")
    if file.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(status_code=400, detail="El archivo debe ser un PDF")

    temp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp:
            temp_path = temp.name
            size = 0
            while data := file.file.read(1024 * 1024):
                size += len(data)
                if settings.max_upload_mb > 0 and size > settings.max_upload_mb * 1024 * 1024:
                    raise HTTPException(status_code=413, detail=f"El máximo es {settings.max_upload_mb} MB")
                temp.write(data)

        job_id = str(uuid4())
        filename = Path(file.filename).name
        with upload_jobs_lock:
            upload_jobs[job_id] = {
                "job_id": job_id,
                "status": "queued",
                "filename": filename,
                "pages": 0,
                "total_chunks": 0,
                "processed_chunks": 0,
            }
        if temp_path is None:
            raise RuntimeError("No se pudo preparar el archivo temporal")
        background_tasks.add_task(process_pdf, temp_path, filename, job_id)
        temp_path = None
        return {"job_id": job_id, "status": "queued", "filename": filename}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Falló el procesamiento del PDF")
        raise HTTPException(status_code=502, detail="No se pudo procesar el PDF. Revisa las claves y los logs del backend.") from exc
    finally:
        file.file.close()
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


@app.get("/upload/{job_id}")
def upload_status(job_id: str) -> dict[str, Any]:
    with upload_jobs_lock:
        job = upload_jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="No se encontró el trabajo de carga")
        return dict(job)


@app.post("/chat")
def chat(payload: ChatRequest) -> dict[str, Any]:
    try:
        query_vector = embed(payload.question, "RETRIEVAL_QUERY")
        matches = supabase.rpc("match_documents", {
            "query_embedding": query_vector,
            "match_threshold": settings.match_threshold,
            "match_count": settings.top_k,
        }).execute().data or []

        context_blocks = []
        sources = []
        for match in matches:
            meta = match.get("metadata") or {}
            source = meta.get("source", "Documento")
            context_blocks.append(f"[Fuente: {source}, fragmento {meta.get('chunk', '?')}]\n{match['content']}")
            sources.append({"source": source, "chunk": meta.get("chunk"), "similarity": match.get("similarity")})

        context = "\n\n---\n\n".join(context_blocks) if context_blocks else "No hay documentos relevantes en la base de conocimiento."
        completion = groq_client.chat.completions.create(
            model=settings.groq_model,
            temperature=0.2,
            max_completion_tokens=900,
            messages=[
                {"role": "system", "content": "Responde en español, con claridad y basándote solo en el contexto dado. Si el contexto no contiene la respuesta, dilo explícitamente. No inventes citas."},
                {"role": "user", "content": f"Contexto recuperado:\n{context}\n\nPregunta: {payload.question}"},
            ],
        )
        answer = completion.choices[0].message.content or "No pude generar una respuesta."
        return {"answer": answer, "sources": sources}
    except Exception as exc:
        logger.exception("Falló la consulta RAG")
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=502, detail="No se pudo completar la consulta. Revisa la configuración del backend.") from exc
