import { useEffect, useRef, useState } from 'react'
import { Activity, ArrowUp, BookOpen, Check, ChevronDown, FileText, FileUp, LoaderCircle, MessageSquare, Plus, Sparkles, UploadCloud } from 'lucide-react'

const API = (import.meta.env.VITE_API_URL || 'http://localhost:8000').replace(/\/$/, '')
const suggestions = ['Resume los puntos principales', '¿Qué recomendaciones aparecen?', 'Busca fechas y plazos importantes', 'Compara las ideas clave']

export default function App() {
  const [view, setView] = useState('chat'), [question, setQuestion] = useState(''), [messages, setMessages] = useState([])
  const [chatBusy, setChatBusy] = useState(false), [file, setFile] = useState(null), [uploadBusy, setUploadBusy] = useState(false), [notice, setNotice] = useState(null)
  const [apiStatus, setApiStatus] = useState('checking')
  const fileRef = useRef(null)

  useEffect(() => {
    const controller = new AbortController()
    fetch(`${API}/health`, { signal: controller.signal })
      .then(response => {
        if (!response.ok) throw new Error('API no disponible')
        setApiStatus('connected')
      })
      .catch(error => {
        if (error.name !== 'AbortError') setApiStatus('offline')
      })
    return () => controller.abort()
  }, [])

  async function sendQuestion(event) {
    event?.preventDefault()
    const text = question.trim()
    if (!text || chatBusy) return
    setMessages(items => [...items, { role: 'user', content: text }]); setQuestion(''); setChatBusy(true)
    try {
      const response = await fetch(`${API}/chat`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question: text }) })
      const data = await response.json()
      if (!response.ok) throw new Error(data.detail || 'No se pudo consultar el asistente.')
      setMessages(items => [...items, { role: 'assistant', content: data.answer, sources: data.sources || [] }])
    } catch (error) { setMessages(items => [...items, { role: 'assistant', content: `Error: ${error.message}`, sources: [], error: true }]) }
    finally { setChatBusy(false) }
  }

  async function uploadDocument(event) {
    event.preventDefault()
    if (!file || uploadBusy) return
    setUploadBusy(true); setNotice(null)
    try {
      const body = new FormData(); body.append('file', file)
      const response = await fetch(`${API}/upload`, { method: 'POST', body }), job = await response.json()
      if (!response.ok) throw new Error(job.detail || 'No se pudo cargar el documento.')
      setNotice({ type: 'info', text: 'Documento recibido. Preparando el procesamiento…' })
      let status = job
      while (status.status === 'queued' || status.status === 'processing') {
        await new Promise(resolve => setTimeout(resolve, 3000))
        const statusResponse = await fetch(`${API}/upload/${job.job_id}`)
        status = await statusResponse.json()
        if (!statusResponse.ok) throw new Error(status.detail || 'No se pudo consultar el progreso de la carga.')
        if (status.status === 'queued' || status.status === 'processing') {
          const progress = status.total_chunks
            ? ` ${status.processed_chunks} de ${status.total_chunks} fragmentos procesados.`
            : ''
          setNotice({ type: 'info', text: `Procesando ${status.filename}.${progress}` })
        }
      }
      if (status.status !== 'completed') throw new Error(status.error || 'No se pudo procesar el PDF.')
      const data = status.result
      setNotice({ type: 'success', text: `${data.filename}: ${data.pages} páginas procesadas en ${data.chunks} fragmentos.` })
      setFile(null); if (fileRef.current) fileRef.current.value = ''
    } catch (error) { setNotice({ type: 'error', text: error.message }) }
    finally { setUploadBusy(false) }
  }

  return <div className="min-h-screen bg-[#f7f8fc] text-slate-800">
    <aside className="fixed inset-y-0 left-0 z-10 hidden w-[248px] flex-col border-r border-slate-200/80 bg-white px-4 py-6 md:flex">
      <div className="mb-10 flex items-center gap-3 px-2"><div className="grid h-9 w-9 place-items-center rounded-xl bg-indigo-600 text-white shadow-lg shadow-indigo-200"><Sparkles size={18}/></div><div><p className="font-semibold tracking-tight">atlas<span className="text-indigo-600">.</span></p><p className="text-[11px] text-slate-400">ASISTENTE DOCUMENTAL</p></div></div>
      <button onClick={() => { setMessages([]); setView('chat') }} className="mb-6 flex items-center justify-center gap-2 rounded-xl bg-indigo-600 px-4 py-3 text-sm font-medium text-white shadow-md shadow-indigo-200 hover:bg-indigo-700"><Plus size={17}/> Nueva conversación</button>
      <p className="mb-2 px-3 text-[10px] font-semibold uppercase tracking-[.16em] text-slate-400">Espacio de trabajo</p>
      <NavButton active={view === 'chat'} onClick={() => setView('chat')} icon={<MessageSquare size={17}/>} label="Chat en Vivo"/>
      <NavButton active={view === 'admin'} onClick={() => setView('admin')} icon={<FileUp size={17}/>} label="Panel de Administración"/>
      <div className="mt-auto rounded-2xl border border-slate-100 bg-slate-50 p-4"><div className="mb-2 flex items-center gap-2 text-xs font-semibold text-slate-700"><Activity size={15} className={apiStatus === 'connected' ? 'text-emerald-500' : apiStatus === 'offline' ? 'text-rose-500' : 'text-amber-500'}/>{apiStatus === 'connected' ? 'API en línea' : apiStatus === 'offline' ? 'API no disponible' : 'Comprobando API…'}</div><p className="text-[11px] leading-relaxed text-slate-400">Estado de conexión con el servicio backend.</p><div className="mt-3 h-1.5 rounded-full bg-slate-200"><div className={`h-full rounded-full ${apiStatus === 'connected' ? 'w-full bg-emerald-400' : apiStatus === 'offline' ? 'w-1/4 bg-rose-400' : 'w-1/2 animate-pulse bg-amber-400'}`}/></div></div>
    </aside>
    <div className="md:pl-[248px]">
      <header className="sticky top-0 z-[5] flex h-[68px] items-center justify-between border-b border-slate-200/70 bg-[#f7f8fc]/90 px-5 backdrop-blur-md md:px-10"><div className="flex items-center gap-3"><div className="grid h-8 w-8 place-items-center rounded-lg bg-indigo-600 text-white md:hidden"><Sparkles size={16}/></div><div><p className="text-sm font-semibold">{view === 'chat' ? 'Chat en Vivo' : 'Panel de Administración'}</p><p className="hidden text-xs text-slate-400 sm:block">{view === 'chat' ? 'Pregunta sobre tus documentos' : 'Gestiona tu base de conocimiento'}</p></div></div><div className="flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-[11px] font-medium text-slate-500"><span className={`h-1.5 w-1.5 rounded-full ${apiStatus === 'connected' ? 'bg-emerald-400' : apiStatus === 'offline' ? 'bg-rose-400' : 'animate-pulse bg-amber-400'}`}/>{apiStatus === 'connected' ? 'API en línea' : apiStatus === 'offline' ? 'API no disponible' : 'Verificando API…'}</div></header>
      <div className="border-b border-slate-200/70 bg-white px-3 py-2 md:hidden"><div className="mx-auto flex max-w-2xl gap-2"><MobileTab active={view === 'chat'} onClick={() => setView('chat')}>Chat en Vivo</MobileTab><MobileTab active={view === 'admin'} onClick={() => setView('admin')}>Administración</MobileTab></div></div>
      {view === 'chat' ? <main className="mx-auto flex min-h-[calc(100vh-68px)] max-w-4xl flex-col px-4 pb-6 pt-8 md:px-10 md:pt-12">
        {messages.length === 0 ? <div className="flex flex-1 flex-col items-center justify-center pb-20 text-center"><div className="mb-6 grid h-16 w-16 place-items-center rounded-[22px] bg-white text-indigo-600 shadow-sm ring-1 ring-slate-200/70"><BookOpen size={27}/></div><p className="mb-3 text-xs font-semibold uppercase tracking-[.18em] text-indigo-600">Tu conocimiento, al instante</p><h1 className="max-w-xl text-3xl font-semibold leading-tight tracking-tight text-slate-900 md:text-[42px]">¿Qué te gustaría <span className="text-indigo-600">descubrir?</span></h1><p className="mt-4 max-w-md text-sm leading-6 text-slate-500">Haz preguntas sobre tus documentos. Atlas buscará la información y te responderá con las fuentes relevantes.</p><div className="mt-9 grid w-full max-w-2xl gap-3 sm:grid-cols-2">{suggestions.map(text => <button key={text} onClick={() => setQuestion(text)} className="rounded-xl border border-slate-200/80 bg-white px-4 py-3 text-left text-xs text-slate-500 shadow-sm transition hover:border-indigo-200 hover:text-indigo-700">{text}</button>)}</div></div> : <div className="flex-1 space-y-6 pb-8 pt-4">{messages.map((message, index) => <article key={index} className={`flex gap-3 ${message.role === 'user' ? 'flex-row-reverse' : ''}`}><div className={`grid h-8 w-8 shrink-0 place-items-center rounded-xl ${message.role === 'user' ? 'bg-slate-200 text-slate-600' : 'bg-indigo-600 text-white'}`}>{message.role === 'user' ? <span className="text-[11px] font-semibold">TÚ</span> : <Sparkles size={15}/>}</div><div className={`max-w-[85%] rounded-2xl px-4 py-3 text-sm leading-6 ${message.role === 'user' ? 'bg-indigo-600 text-white' : 'border border-slate-200/70 bg-white text-slate-700 shadow-sm'} ${message.error ? 'text-rose-600' : ''}`}><p className="whitespace-pre-wrap">{message.content}</p>{message.sources?.length > 0 && <details className="group mt-3 border-t border-slate-100 pt-2"><summary className="flex cursor-pointer list-none items-center gap-1.5 text-xs font-medium text-indigo-600"><FileText size={13}/> {message.sources.length} fuentes consultadas <ChevronDown size={13} className="transition group-open:rotate-180"/></summary><div className="mt-2 space-y-1.5">{message.sources.map((source, i) => <div key={i} className="flex items-center justify-between rounded-lg bg-slate-50 px-3 py-2 text-xs"><span className="truncate font-medium text-slate-600">{source.source} · fragmento {source.chunk}</span><span className="ml-3 text-slate-400">{Math.round((source.similarity || 0) * 100)}%</span></div>)}</div></details>}</div></article>)}{chatBusy && <div className="flex gap-3"><div className="grid h-8 w-8 place-items-center rounded-xl bg-indigo-600 text-white"><Sparkles size={15}/></div><div className="flex items-center gap-2 rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-400"><LoaderCircle size={15} className="animate-spin text-indigo-500"/> Buscando en tus documentos…</div></div>}</div>}
        <form onSubmit={sendQuestion} className="sticky bottom-0 rounded-2xl border border-slate-200 bg-white p-2 shadow-lg shadow-slate-200/60"><textarea value={question} onChange={e => setQuestion(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendQuestion(e) } }} rows="1" placeholder="Escribe tu pregunta…" className="max-h-36 min-h-12 w-full resize-y border-0 bg-transparent px-3 py-3 text-sm outline-none placeholder:text-slate-400"/><div className="flex items-center justify-between px-2 pb-1"><p className="text-[10px] text-slate-400">Enter para enviar · Shift + Enter para nueva línea</p><button disabled={!question.trim() || chatBusy} aria-label="Enviar pregunta" className="grid h-9 w-9 place-items-center rounded-xl bg-indigo-600 text-white hover:bg-indigo-700"><ArrowUp size={17}/></button></div></form>
      </main> :       <main className="mx-auto max-w-4xl px-4 py-8 md:px-10 md:py-12"><div className="mb-8"><p className="mb-2 text-xs font-semibold uppercase tracking-[.16em] text-indigo-600">Base de conocimiento</p><h1 className="text-3xl font-semibold tracking-tight text-slate-900">Añade documentos</h1><p className="mt-2 max-w-xl text-sm leading-6 text-slate-500">Sube archivos PDF para que Atlas pueda encontrar y responder preguntas con su contenido.</p></div><form onSubmit={uploadDocument} className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm md:p-8"><div onClick={() => fileRef.current?.click()} onDragOver={e => e.preventDefault()} onDrop={e => { e.preventDefault(); const dropped = e.dataTransfer.files?.[0]; if (dropped) setFile(dropped) }} className="flex cursor-pointer flex-col items-center rounded-2xl border-2 border-dashed border-indigo-200 bg-indigo-50/40 px-5 py-12 text-center transition hover:border-indigo-400 hover:bg-indigo-50"><input ref={fileRef} type="file" accept="application/pdf,.pdf" className="hidden" onChange={e => setFile(e.target.files?.[0] || null)}/><div className="mb-4 grid h-14 w-14 place-items-center rounded-2xl bg-white text-indigo-600 shadow-sm"><UploadCloud size={24}/></div><p className="text-sm font-semibold text-slate-800">{file ? file.name : 'Arrastra tu PDF aquí'}</p><p className="mt-1 text-xs text-slate-400">o haz clic para explorar · sin límite artificial de la aplicación</p></div>{notice && <div className={`mt-5 flex items-start gap-2 rounded-xl px-4 py-3 text-sm ${notice.type === 'success' ? 'bg-emerald-50 text-emerald-700' : notice.type === 'info' ? 'bg-indigo-50 text-indigo-700' : 'bg-rose-50 text-rose-700'}`}>{notice.type === 'success' ? <Check size={17} className="mt-0.5 shrink-0"/> : notice.type === 'info' ? <LoaderCircle size={17} className="mt-0.5 shrink-0 animate-spin"/> : <Activity size={17} className="mt-0.5 shrink-0"/>}<span>{notice.text}</span></div>}<div className="mt-6 flex flex-col justify-between gap-4 sm:flex-row sm:items-center"><div><p className="text-xs font-medium text-slate-700">Procesamiento temporal</p><p className="mt-1 text-xs text-slate-400">El PDF temporal se elimina después de extraer el texto. La plataforma y las cuotas de IA aún aplican.</p></div><button disabled={!file || uploadBusy} className="flex items-center justify-center gap-2 rounded-xl bg-indigo-600 px-5 py-3 text-sm font-semibold text-white shadow-md shadow-indigo-200 hover:bg-indigo-700">{uploadBusy ? <><LoaderCircle size={16} className="animate-spin"/> Procesando…</> : <><FileUp size={16}/> Subir documento</>}</button></div></form><div className="mt-6 rounded-2xl border border-slate-200/70 bg-white/70 p-5"><div className="flex gap-3"><div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-amber-50 text-amber-600"><Sparkles size={16}/></div><div><p className="text-sm font-medium text-slate-700">Mejores resultados</p><p className="mt-1 text-xs leading-5 text-slate-500">Usa PDFs con texto seleccionable. Los escaneados necesitan OCR antes de cargarlos.</p></div></div></div></main>}
    </div>
  </div>
}

function NavButton({ active, onClick, icon, label }) { return <button onClick={onClick} className={`flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-sm ${active ? 'bg-indigo-50 font-medium text-indigo-700' : 'text-slate-500 hover:bg-slate-50'}`}>{icon}{label}</button> }
function MobileTab({ active, onClick, children }) { return <button onClick={onClick} className={`flex-1 rounded-lg py-2 text-xs ${active ? 'bg-indigo-50 text-indigo-700' : 'text-slate-500'}`}>{children}</button> }
