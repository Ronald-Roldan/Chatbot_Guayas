# RAG Cloud Starter

Starter full-stack para consultar PDFs: FastAPI, Supabase + pgvector, Gemini embeddings, Groq y React/Vite/Tailwind.

## Disponibilidad de modelos

Los IDs pedidos originalmente (`text-embedding-004` y `llama-3.3-70b-versatile`) ya fueron retirados. Para que el proyecto funcione hoy, usa por defecto `gemini-embedding-001` (768 dimensiones) y `openai/gpt-oss-20b`. Los IDs son configurables mediante `GOOGLE_EMBEDDING_MODEL`, `GROQ_MODEL` y `EMBEDDING_DIMENSIONS`. Cambiar la dimensión requiere recrear la columna vectorial y reindexar el corpus.

## Estructura

```text
.
├── backend/
│   ├── app/main.py
│   ├── requirements.txt
│   ├── render.yaml
│   └── .env.example
├── frontend/
│   ├── src/App.jsx, main.jsx, index.css
│   ├── package.json, vite.config.js, tailwind.config.js
│   ├── postcss.config.js, index.html, .env.example
├── supabase/schema.sql
├── .gitignore
└── README.md
```

## Inicio local en Windows

1. Crea un proyecto Supabase y ejecuta `supabase/schema.sql` en el SQL Editor.
2. Instala Python 3.12 y Node.js 20 o superior. Obtén claves de Google AI Studio y Groq; nunca las pongas en el frontend.
3. En PowerShell, configura el backend:

   ```powershell
   cd backend
   Copy-Item .env.example .env
   # Edita .env y reemplaza los valores de ejemplo por tus claves.
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   uvicorn app.main:app --reload
   ```

4. En otra terminal, configura el frontend:

   ```powershell
   cd frontend
   Copy-Item .env.example .env
   npm install
   npm run dev
   ```

5. Abre la URL local que muestra Vite. `VITE_API_URL` debe apuntar a `http://localhost:8000`.

## Despliegue

1. Sube el proyecto a GitHub. En Render crea un Blueprint desde ese repositorio y selecciona `backend/render.yaml`. Durante la creación, proporciona `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `GOOGLE_API_KEY` y `GROQ_API_KEY`; Render los conserva como variables privadas. Render desplegará el backend automáticamente cuando reciba cambios en la rama conectada.
2. Crea un proyecto de Cloudflare Pages con el nombre que quieras (por ejemplo, `rag-cloud-starter`). Para esta automatización, no configures un build Git en Cloudflare: GitHub Actions hará el build y la publicación.
3. En GitHub, abre **Settings → Secrets and variables → Actions** y configura:
   - **Secrets:** `CLOUDFLARE_API_TOKEN` (token con permisos de Pages Edit) y `CLOUDFLARE_ACCOUNT_ID`.
   - **Variables:** `CLOUDFLARE_PAGES_PROJECT_NAME` (el nombre exacto del proyecto Pages) y `VITE_API_URL` (URL pública de Render, sin slash final).
4. Al subir cambios a `main`, Render publica el backend y [.github/workflows/deploy-frontend.yml](.github/workflows/deploy-frontend.yml) construye `frontend` y lo publica en Cloudflare Pages. También puedes iniciar el workflow manualmente desde **Actions** en GitHub.

La API usa CORS abierto por defecto. Si lo restringes mediante `ALLOWED_ORIGINS`, añade el dominio público de Cloudflare Pages. Para despliegue local, sigue la sección de inicio local más arriba.

Backend env: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `GOOGLE_API_KEY`, `GROQ_API_KEY`, opcionales `GOOGLE_EMBEDDING_MODEL`, `GROQ_MODEL`, `EMBEDDING_DIMENSIONS`, `MAX_UPLOAD_MB`, `TOP_K`, `MATCH_THRESHOLD`, `ALLOWED_ORIGINS`. `MAX_UPLOAD_MB=0` desactiva el límite artificial de tamaño de la aplicación; Render, la memoria/almacenamiento disponibles y las cuotas de Google/Supabase siguen imponiendo sus propios límites. `/upload` devuelve un identificador de trabajo y procesa el PDF en segundo plano; consulta `GET /upload/{job_id}` para el progreso y resultado. Los embeddings de documentos se solicitan en lotes y el backend reintenta cuando Google devuelve una cuota temporal agotada. Frontend env: `VITE_API_URL` (local: `http://localhost:8000`).

La recuperación combina similitud vectorial (umbral predeterminado `0.63`) con búsqueda textual que ignora términos interrogativos y palabras funcionales comunes en español e inglés. Devuelve fragmentos vecinos en orden y usa un identificador único por carga para que dos PDFs con el mismo nombre no se mezclen. Si una carga falla después de insertar algunos lotes, elimina los fragmentos parciales de esa carga. La tabla usa búsqueda vectorial exacta para el corpus pequeño actual; conviene medir y elegir un índice ANN si el número de fragmentos crece mucho.

Las pruebas se ejecutan desde `backend` con `python -m unittest discover -s tests -v`. Cubren el flujo HTTP de carga, extracción de tres PDFs de temas distintos, consultas con fuentes, preguntas sin resultados, validación de entradas, nombres repetidos y limpieza después de una inserción parcial. Las claves externas se simulan y no se envían llamadas a los proveedores.

## Seguridad y coste

`/upload` es público y no incluye autenticación ni cuotas. CORS abierto no autentica ni protege la API. La clave service role solo reside en Render. Antes de producción añade autenticación, límites de uso y aislamiento de documentos por usuario. La tabla está pensada para un corpus compartido. Los servicios gratuitos pueden dormir, limitar uso o cambiar sus cuotas; un coste de $0 no puede garantizarse para cualquier volumen. Las claves que aparecieron en archivos de configuración deben revocarse y reemplazarse antes de desplegar, también si ya se guardaron en el historial de Git.
