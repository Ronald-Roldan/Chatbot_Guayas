import os
import re
import unicodedata
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault(
    "SUPABASE_SERVICE_ROLE_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoic2VydmljZV9yb2xlIn0.c2lnbmF0dXJl",
)
os.environ.setdefault("GOOGLE_API_KEY", "test-google-key")
os.environ.setdefault("GROQ_API_KEY", "test-groq-key")

import fitz
from fastapi.testclient import TestClient

from app import main


class InMemoryDocuments:
    def __init__(self):
        self.rows = []
        self.pending = None
        self.delete_filter = None

    def insert(self, rows):
        self.pending = list(rows)
        return self

    def delete(self):
        self.delete_filter = None
        return self

    def contains(self, column, value):
        self.delete_filter = (column, value)
        return self

    def execute(self):
        if self.pending is not None:
            self.rows.extend(self.pending)
            self.pending = None
        if self.delete_filter:
            column, value = self.delete_filter
            self.rows = [row for row in self.rows if row["metadata"].get(column) != value]
        return SimpleNamespace(data=[])


class InMemorySupabase:
    def __init__(self):
        self.documents = InMemoryDocuments()
        self.rpc_calls = []

    def table(self, name):
        if name != "documents":
            raise AssertionError(f"Unexpected table: {name}")
        return self.documents

    def rpc(self, name, params):
        self.rpc_calls.append((name, params))
        if name != "match_documents_hybrid":
            raise AssertionError(f"Unexpected RPC: {name}")

        normalize = lambda value: unicodedata.normalize("NFKD", value.casefold()).encode("ascii", "ignore").decode()
        terms = set(re.findall(r"[a-z0-9]+", normalize(params["query_text"])))
        scored = []
        for row in self.documents.rows:
            content_terms = set(re.findall(r"[a-z0-9]+", normalize(row["content"])))
            overlap = len(terms & content_terms)
            if overlap:
                scored.append((overlap, row))
        matches = [
            {
                "id": index,
                "content": row["content"],
                "metadata": row["metadata"],
                "similarity": 0.8,
            }
            for index, (_, row) in enumerate(sorted(scored, key=lambda item: item[0], reverse=True)[:2])
        ]
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=matches))


class GenericRagWorkflowTests(unittest.TestCase):
    documents = {
        "coffee.pdf": "Para conservar los granos de café, guárdalos en un recipiente hermético, seco y lejos de la luz.",
        "garden.pdf": "Riega las plantas de tomate por la mañana y revisa que la tierra drene bien.",
        "printer.pdf": "Si una impresora 3D deja de extruir filamento, verifica que la boquilla no esté obstruida.",
    }
    questions = {
        "¿Cómo conservar los granos de café?": ("granos de café", "coffee.pdf"),
        "¿Cuándo debo regar el tomate?": ("plantas de tomate", "garden.pdf"),
        "¿Qué revisar si la impresora 3D no extruye filamento?": ("impresora 3D", "printer.pdf"),
    }

    @staticmethod
    def pdf_bytes(text):
        with fitz.open() as pdf:
            page = pdf.new_page()
            page.insert_text((72, 72), text)
            return pdf.tobytes()

    def test_upload_and_chat_work_for_unrelated_document_topics(self):
        database = InMemorySupabase()
        model = Mock()
        model.chat.completions.create.return_value.choices = [
            SimpleNamespace(message=SimpleNamespace(content="Respuesta basada en el documento cargado."))
        ]
        vector = [0.1] * 768

        with (
            patch.object(main, "supabase", database),
            patch.object(main, "embed_many", side_effect=lambda batch, _task: [vector for _ in batch]),
            patch.object(main, "embed", return_value=vector),
            patch.object(main, "groq_client", model),
            TestClient(main.app) as client,
        ):
            uploaded_jobs = {}
            for filename, content in self.documents.items():
                response = client.post(
                    "/upload",
                    files={"file": (filename, self.pdf_bytes(content), "application/pdf")},
                )
                self.assertEqual(response.status_code, 202, response.text)
                job = response.json()
                uploaded_jobs[filename] = job["job_id"]

                status = client.get(f"/upload/{job['job_id']}")
                self.assertEqual(status.status_code, 200)
                self.assertEqual(status.json()["status"], "completed")

            self.assertEqual(len(database.documents.rows), len(self.documents))
            self.assertEqual(
                {row["metadata"]["source"] for row in database.documents.rows},
                set(self.documents),
            )
            self.assertEqual(len({row["metadata"]["document_id"] for row in database.documents.rows}), 3)

            for question, (expected_context, expected_source) in self.questions.items():
                response = client.post("/chat", json={"question": question})
                self.assertEqual(response.status_code, 200, response.text)
                result = response.json()
                self.assertEqual(result["answer"], "Respuesta basada en el documento cargado.")
                self.assertTrue(result["sources"])
                prompt = model.chat.completions.create.call_args.kwargs["messages"][0]["content"].casefold()
                self.assertIn(expected_context.casefold(), prompt)
                self.assertIn(expected_source, {source["source"] for source in result["sources"]})

            sent_questions = [params["query_text"] for _, params in database.rpc_calls]
            self.assertEqual(sent_questions, list(self.questions))
            self.assertTrue(all("query_article" not in params for _, params in database.rpc_calls))

    def test_http_contract_reports_invalid_upload_and_question(self):
        with TestClient(main.app) as client:
            self.assertEqual(client.get("/health").json(), {"status": "ok"})
            self.assertEqual(client.post("/upload", files={"file": ("notes.txt", b"not a pdf", "text/plain")}).status_code, 400)
            self.assertEqual(client.post("/chat", json={"question": "x"}).status_code, 422)
            self.assertEqual(client.get("/upload/missing-job").status_code, 404)

    def test_chat_can_abstain_when_no_document_matches(self):
        database = InMemorySupabase()
        model = Mock()
        model.chat.completions.create.return_value.choices = [
            SimpleNamespace(message=SimpleNamespace(content="No se menciona en los documentos."))
        ]

        with (
            patch.object(main, "supabase", database),
            patch.object(main, "embed", return_value=[0.1] * 768),
            patch.object(main, "groq_client", model),
            TestClient(main.app) as client,
        ):
            response = client.post("/chat", json={"question": "¿Cuál es la presión de neumáticos indicada?"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["sources"], [])
        prompt = model.chat.completions.create.call_args.kwargs["messages"][0]["content"]
        self.assertIn("No hay documentos relevantes", prompt)


if __name__ == "__main__":
    unittest.main()
