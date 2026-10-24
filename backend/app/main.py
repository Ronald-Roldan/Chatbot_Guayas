from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import fitz
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from google import genai
from google.genai import types
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
    max_upload_mb: int = 15
    top_k: int = 5
    match_threshold: float = 0.25
    allowed_origins: str = "*"


settings = Settings()
supabase: Client = create_client(settings.supabase_url, settings.supabase_service_role_key)
genai_client = genai.Client(api_key=settings.google_api_key)
groq_client = Groq(api_key=settings.groq_api_key, base_url="https://api.groq.com")

app = FastAPI(title="RAG API", version="1.0.0")
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
    result = genai_client.models.embed_content(
        model=settings.google_embedding_model,
        contents=text,
        config=types.EmbedContentConfig(
            task_type=task_type,
            output_dimensionality=settings.embedding_dimensions,
        ),
    )
    if not result.embeddings or not result.embeddings[0].values:
        raise RuntimeError("Google devolvió un embedding vacío")
    return list(result.embeddings[0].values)


@app.get("/")
def root() -> dict[str, str]:
    return {"name": "RAG API", "status": "ok", "docs": "/docs"}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/upload")
def upload_pdf(file: UploadFile = File(...)) -> dict[str, Any]:
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
                if size > settings.max_upload_mb * 1024 * 1024:
                    raise HTTPException(status_code=413, detail=f"El máximo es {settings.max_upload_mb} MB")
                temp.write(data)

        with fitz.open(temp_path) as pdf:
            pages = [page.get_text("text") for page in pdf]
        full_text = "\n".join(pages).strip()
        if not full_text:
            raise HTTPException(status_code=422, detail="No se encontró texto. El PDF podría ser un escaneo sin OCR.")

        chunks = splitter.split_text(full_text)
        rows = []
        for index, chunk in enumerate(chunks):
            rows.append({
                "content": chunk,
                "embedding": embed(chunk, "RETRIEVAL_DOCUMENT"),
                "metadata": {"source": Path(file.filename).name, "chunk": index, "pages": len(pages)},
            })
        for offset in range(0, len(rows), 50):
            supabase.table("documents").insert(rows[offset : offset + 50]).execute()
        return {"filename": Path(file.filename).name, "pages": len(pages), "chunks": len(chunks), "message": "PDF procesado"}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Falló el procesamiento del PDF")
        raise HTTPException(status_code=502, detail="No se pudo procesar el PDF. Revisa las claves y los logs del backend.") from exc
    finally:
        file.file.close()
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


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
        raise HTTPException(status_code=502, detail="No se pudo completar la consulta. Revisa la configuración del backend.") from exc
