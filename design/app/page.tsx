'use client'

import { useEffect, useState } from 'react'
import {
  Activity, Check, CircleHelp, Clipboard, FileCog, Globe2, LockKeyhole,
  Power, RefreshCw, Settings2, Shield, Terminal, Wifi, X, Zap,
} from 'lucide-react'

type Page = 'home' | 'settings' | 'log'
type Status = 'idle' | 'connecting' | 'connected' | 'error'

const initialLogs = [
  { time: 'AGORA', text: 'Aplicação iniciada', tone: 'ok' },
  { time: 'AGORA', text: 'Arquivos do serviço verificados', tone: 'ok' },
  { time: '—', text: 'Aguardando conexão', tone: 'muted' },
]

export default function Page() {
  const [page, setPage] = useState<Page>('home')
  const [status, setStatus] = useState<Status>('idle')
  const [progress, setProgress] = useState(0)
  const [logs, setLogs] = useState(initialLogs)
  const [port, setPort] = useState('9050')
  const [autoDiscord, setAutoDiscord] = useState(true)
  const [closeTray, setCloseTray] = useState(true)
  const [autoUpdate, setAutoUpdate] = useState(true)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (status !== 'connecting') return
    const timer = window.setInterval(() => {
      setProgress((value) => {
        const next = Math.min(value + 7, 100)
        if (next === 100) {
          window.clearInterval(timer)
          setStatus('connected')
          setLogs((current) => [{ time: 'AGORA', text: 'Serviço conectado na porta SOCKS5 ' + port, tone: 'ok' }, { time: 'AGORA', text: 'Aplicativo configurado para usar o proxy', tone: 'discord' }, ...current])
        }
        return next
      })
    }, 140)
    return () => window.clearInterval(timer)
  }, [status, port])

  function connect() {
    if (status === 'connected') {
      setStatus('idle'); setProgress(0)
      setLogs((current) => [{ time: 'AGORA', text: 'Conexão encerrada pelo usuário', tone: 'muted' }, ...current])
    } else {
      setStatus('connecting'); setProgress(8)
      setLogs((current) => [{ time: 'AGORA', text: 'Iniciando o serviço e preparando o proxy...', tone: 'accent' }, ...current])
    }
  }

  function copyProxy() {
    navigator.clipboard?.writeText(`socks5://127.0.0.1:${port}`)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1600)
  }

  const connected = status === 'connected'
  const connecting = status === 'connecting'
  const title = page === 'home' ? 'Conexão' : page === 'settings' ? 'Configurações' : 'Atividade'

  return (
    <main className="tor-shell">
      <div className="tor-window">
        <header className="tor-titlebar">
          <div className="tor-brand"><div className="onion-mark"><Shield /></div><div><strong>Obscure Proxy</strong><span>Private connection</span></div></div>
          <div className="titlebar-status"><i className={connected ? 'green' : connecting ? 'purple' : ''} />{connected ? 'Conectado' : connecting ? 'Conectando' : 'Desconectado'}</div>
          <div className="window-controls"><button aria-label="Minimizar">—</button><button aria-label="Fechar"><X /></button></div>
        </header>
        <div className="tor-layout">
          <aside className="tor-sidebar">
            <div className="sidebar-caption">PRIVATE CONNECTION</div>
            <nav className="tor-nav" aria-label="Navegação principal">
              <NavButton active={page === 'home'} icon={<Zap />} label="Conexão" onClick={() => setPage('home')} />
              <NavButton active={page === 'settings'} icon={<Settings2 />} label="Configurações" onClick={() => setPage('settings')} />
              <NavButton active={page === 'log'} icon={<Activity />} label="Atividade" onClick={() => setPage('log')} />
            </nav>
            <div className="sidebar-bottom"><div className="side-line" /><div className="runtime"><span>STATUS DO SISTEMA</span><strong><i className={connected ? 'green' : ''} />{connected ? 'Proxy ativo' : 'Pronto para iniciar'}</strong></div><span className="version">v1.0.0</span></div>
          </aside>
          <section className="tor-content">
            <div className="page-heading"><div><span className="section-kicker">OBSCURE PROXY / {title.toUpperCase()}</span><h1>{page === 'home' ? 'Conecte sua sessão com privacidade' : title}</h1><p>{page === 'home' ? 'Proteja sua conexão com um proxy local privado.' : page === 'settings' ? 'Ajuste o comportamento do launcher.' : 'Acompanhe o que acontece nesta sessão.'}</p></div><button className="help-button" onClick={() => setLogs((current) => [{ time: 'AGORA', text: 'Central de ajuda aberta', tone: 'muted' }, ...current])}><CircleHelp /> Ajuda</button></div>
            {page === 'home' && <Home status={status} progress={progress} port={port} connected={connected} connecting={connecting} copied={copied} onConnect={connect} onCopy={copyProxy} onSettings={() => setPage('settings')} />}
            {page === 'settings' && <Settings port={port} setPort={setPort} autoDiscord={autoDiscord} setAutoDiscord={setAutoDiscord} closeTray={closeTray} setCloseTray={setCloseTray} autoUpdate={autoUpdate} setAutoUpdate={setAutoUpdate} />}
            {page === 'log' && <Log logs={logs} clear={() => setLogs([])} />}
          </section>
        </div>
      </div>
    </main>
  )
}

function NavButton({ active, icon, label, onClick }: { active: boolean; icon: React.ReactNode; label: string; onClick: () => void }) {
  return <button className={`tor-nav-button ${active ? 'active' : ''}`} onClick={onClick}>{icon}<span>{label}</span></button>
}

function Home({ status, progress, port, connected, connecting, copied, onConnect, onCopy, onSettings }: { status: Status; progress: number; port: string; connected: boolean; connecting: boolean; copied: boolean; onConnect: () => void; onCopy: () => void; onSettings: () => void }) {
  const statusLabel = connected ? 'CONEXÃO ATIVA' : connecting ? 'INICIANDO CONEXÃO' : 'DESCONECTADO'
  return <div className="home-page">
    <div className={`hero-card ${connected ? 'connected' : connecting ? 'connecting' : ''}`}>
      <div className="hero-top"><span className="status-chip"><i /> {statusLabel}</span><span className="tor-address">SOCKS5 / 127.0.0.1:{port}</span></div>
      <div className="orb-wrap"><div className="orb-halo" /><div className="orb"><div className="orb-progress" style={{ transform: `rotate(${progress * 3.6 - 45}deg)` }} /><div className="orb-core"><Shield /></div></div><span className="orb-label">{connected ? 'Protegido' : connecting ? `${progress}%` : 'Privacidade'}</span></div>
      <div className="hero-copy"><h2>{connected ? 'Conexão segura ativa' : connecting ? 'Preparando conexão segura...' : 'Tudo pronto para começar'}</h2><p>{connected ? 'O aplicativo está usando o proxy local.' : connecting ? 'Inicializando o serviço e negociando uma rota...' : 'Inicie o proxy para rotear sua sessão por uma rede privada.'}</p></div>
      {connecting && <div className="progress-track"><span style={{ width: `${progress}%` }} /></div>}
      <button className={`main-connect ${connected ? 'disconnect' : ''}`} onClick={onConnect}>{connected ? 'Desconectar' : connecting ? 'Cancelar conexão' : 'Iniciar conexão'}<Power /></button>
    </div>
    <div className="quick-grid"><div className="quick-card"><div className="quick-icon purple"><Terminal /></div><div><span>PROXY LOCAL</span><strong>127.0.0.1:{port}</strong><small>Porta SOCKS5 configurada</small></div><button onClick={onCopy} aria-label="Copiar endereço do proxy">{copied ? <Check /> : <Clipboard />}</button></div><div className="quick-card"><div className="quick-icon blue"><Wifi /></div><div><span>APLICATIVO</span><strong>{connected ? 'Proxy configurado' : 'Aguardando serviço'}</strong><small>{connected ? 'Tráfego roteado com segurança' : 'Será preparado automaticamente'}</small></div></div></div>
    <div className="notice"><FileCog /><div><strong>Arquivos empacotados com segurança</strong><span>Os componentes necessários são extraídos apenas durante a execução.</span></div><button onClick={onSettings}>Configurar</button></div>
  </div>
}

function Settings({ port, setPort, autoDiscord, setAutoDiscord, closeTray, setCloseTray, autoUpdate, setAutoUpdate }: { port: string; setPort: (v: string) => void; autoDiscord: boolean; setAutoDiscord: (v: boolean) => void; closeTray: boolean; setCloseTray: (v: boolean) => void; autoUpdate: boolean; setAutoUpdate: (v: boolean) => void }) {
  return <div className="settings-page"><div className="settings-card"><div className="card-title"><div><span className="section-kicker">CONNECTION CONFIGURATION</span><h2>Preferências do launcher</h2></div><FileCog /></div><label className="field-label">Porta SOCKS5<input value={port} onChange={(e) => setPort(e.target.value.replace(/\D/g, '').slice(0, 5))} /><small>Porta local usada pelo serviço e pelo aplicativo.</small></label><Setting title="Iniciar Discord automaticamente" description="Abra o Discord assim que o Tor estiver pronto." value={autoDiscord} setValue={setAutoDiscord} icon={<Wifi />} /><Setting title="Minimizar para a bandeja" description="Feche a janela sem encerrar o processo em segundo plano." value={closeTray} setValue={setCloseTray} icon={<Shield />} /><Setting title="Verificar atualizações" description="Confira novas versões no GitHub ao iniciar." value={autoUpdate} setValue={setAutoUpdate} icon={<RefreshCw />} /></div><div className="settings-card info-card"><Globe2 /><div><span className="section-kicker">RUNTIME</span><h2>Dados temporários</h2><p>Os arquivos do Tor ficam em uma pasta temporária e são removidos quando o launcher é encerrado.</p></div></div></div>
}

function Setting({ title, description, value, setValue, icon }: { title: string; description: string; value: boolean; setValue: (v: boolean) => void; icon: React.ReactNode }) { return <div className="setting"><div className="setting-icon">{icon}</div><div><strong>{title}</strong><span>{description}</span></div><button className={`toggle ${value ? 'on' : ''}`} aria-pressed={value} onClick={() => setValue(!value)}><i /></button></div> }

function Log({ logs, clear }: { logs: { time: string; text: string; tone: string }[]; clear: () => void }) { return <div className="log-page"><div className="log-toolbar"><div><span className="section-kicker">SESSION LOG</span><h2>Atividade recente</h2></div><button onClick={clear}>Limpar atividade</button></div><div className="log-card">{logs.length ? logs.map((log, i) => <div className="log-entry" key={`${log.text}-${i}`}><span>{log.time}</span><i className={log.tone} /><strong>{log.text}</strong><Check /></div>) : <div className="empty"><Activity /> Nenhuma atividade nesta sessão.</div>}</div></div> }
