import { useState, useRef, useEffect, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'
import {
  Brain,
  CheckCircle,
  ChevronRight,
  Clock,
  Code,
  Eye,
  EyeOff,
  FileCode,
  Folder,
  Key,
  MessageSquare,
  Play,
  RefreshCw,
  RotateCcw,
  Save,
  Settings,
  Sparkles,
  Users,
  XCircle,
  Zap,
  AlertTriangle,
} from 'lucide-react'

const PROVIDERS = [
  { id: 'anthropic', name: 'Claude (Anthropic)', color: 'bg-orange-500' },
  { id: 'openai', name: 'GPT (OpenAI)', color: 'bg-green-500' },
]

const STATUS_COLORS = {
  pending: 'bg-slate-500',
  in_progress: 'bg-blue-500',
  consensus: 'bg-green-500',
  deadlock: 'bg-yellow-500',
  error: 'bg-red-500',
}

const ROLE_STYLES = {
  proposer: {
    bg: 'bg-blue-900/40',
    border: 'border-blue-500/50',
    icon: Sparkles,
    label: 'PROPOSER',
    labelColor: 'text-blue-400',
  },
  critic: {
    bg: 'bg-purple-900/40',
    border: 'border-purple-500/50',
    icon: Eye,
    label: 'CRITIC',
    labelColor: 'text-purple-400',
  },
  system: {
    bg: 'bg-slate-800/60',
    border: 'border-slate-600/50',
    icon: Settings,
    label: 'SYSTEM',
    labelColor: 'text-slate-400',
  },
}

function App() {
  // Configuration state
  const [anthropicKey, setAnthropicKey] = useState('')
  const [openaiKey, setOpenaiKey] = useState('')
  const [showKeys, setShowKeys] = useState(false)
  const [keysConfigured, setKeysConfigured] = useState({ anthropic: false, openai: false })

  // Debate settings
  const [projectPath, setProjectPath] = useState('')
  const [task, setTask] = useState('')
  const [proposerProvider, setProposerProvider] = useState('anthropic')
  const [criticProvider, setCriticProvider] = useState('openai')
  const [maxRounds, setMaxRounds] = useState(7)

  // Debate state
  const [sessionId, setSessionId] = useState(null)
  const [messages, setMessages] = useState([])
  const [status, setStatus] = useState('idle')
  const [isRunning, setIsRunning] = useState(false)
  const [currentRound, setCurrentRound] = useState(0)
  const [totalTokens, setTotalTokens] = useState(0)

  // Apply code state
  const [diffPreview, setDiffPreview] = useState(null)
  const [applyStatus, setApplyStatus] = useState(null) // 'loading', 'success', 'error'
  const [autoApply, setAutoApply] = useState(true) // Auto-apply after validation
  const [validationResult, setValidationResult] = useState(null)

  // WebSocket ref
  const wsRef = useRef(null)
  const messagesEndRef = useRef(null)

  // Auto-scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // Check API status on mount and load saved config
  useEffect(() => {
    fetch('/api/status')
      .then(r => r.json())
      .then(data => {
        setKeysConfigured({
          anthropic: data.providers?.includes('anthropic'),
          openai: data.providers?.includes('openai'),
        })
        // Load saved project path
        if (data.project_path && !projectPath) {
          setProjectPath(data.project_path)
        }
      })
      .catch(() => {})
  }, [])

  const configureKeys = async (saveProjectPath = false) => {
    try {
      const response = await fetch('/api/configure', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          anthropic_key: anthropicKey || undefined,
          openai_key: openaiKey || undefined,
          project_path: saveProjectPath ? projectPath : undefined,
        }),
      })
      const data = await response.json()

      setKeysConfigured({
        anthropic: data.providers?.includes('anthropic') || keysConfigured.anthropic,
        openai: data.providers?.includes('openai') || keysConfigured.openai,
      })

      // Clear keys from state after configuring
      if (anthropicKey) setAnthropicKey('')
      if (openaiKey) setOpenaiKey('')
    } catch (error) {
      console.error('Failed to configure keys:', error)
    }
  }

  const startDebate = useCallback(async () => {
    if (!task.trim() || !projectPath.trim()) {
      alert('Please enter a task and project path')
      return
    }

    // Get session ID
    const response = await fetch('/api/debate/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        task,
        project_path: projectPath,
        proposer_provider: proposerProvider,
        critic_provider: criticProvider,
        max_rounds: maxRounds,
      }),
    })

    if (!response.ok) {
      const error = await response.json()
      alert(error.detail || 'Failed to start debate')
      return
    }

    const data = await response.json()
    setSessionId(data.session_id)
    setMessages([])
    setStatus('connecting')
    setIsRunning(true)
    setCurrentRound(0)
    setTotalTokens(0)

    // Connect WebSocket
    const wsUrl = `ws://${window.location.host}/ws/debate/${data.session_id}`
    const ws = new WebSocket(wsUrl)
    wsRef.current = ws

    ws.onopen = () => {
      setStatus('in_progress')
      ws.send(JSON.stringify({
        action: 'start',
        task,
        project_path: projectPath,
        proposer_provider: proposerProvider,
        critic_provider: criticProvider,
        max_rounds: maxRounds,
      }))
    }

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data)

      if (data.type === 'message') {
        setMessages(prev => [...prev, data])
        setTotalTokens(prev => prev + (data.tokens_used || 0))

        // Update round counter for proposer messages
        if (data.role === 'proposer') {
          setCurrentRound(prev => prev + 1)
        }
      } else if (data.type === 'debate_complete') {
        setStatus(data.status)
        setIsRunning(false)
      } else if (data.type === 'error') {
        setStatus('error')
        setIsRunning(false)
        setMessages(prev => [...prev, {
          role: 'system',
          content: `Error: ${data.message}`,
          timestamp: new Date().toISOString(),
        }])
      }
    }

    ws.onclose = () => {
      setIsRunning(false)
    }

    ws.onerror = () => {
      setStatus('error')
      setIsRunning(false)
    }
  }, [task, projectPath, proposerProvider, criticProvider, maxRounds])

  const stopDebate = () => {
    if (wsRef.current) {
      wsRef.current.close()
    }
    setIsRunning(false)
    setStatus('stopped')
  }

  const resumeDebate = useCallback(async (switchProvider = null) => {
    if (!sessionId) {
      alert('No session to resume')
      return
    }

    setStatus('connecting')
    setIsRunning(true)

    // Connect WebSocket for resume
    const wsUrl = `ws://${window.location.host}/ws/debate/${sessionId}`
    const ws = new WebSocket(wsUrl)
    wsRef.current = ws

    ws.onopen = () => {
      setStatus('in_progress')
      ws.send(JSON.stringify({
        action: 'resume',
        switch_provider: switchProvider,
      }))
    }

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data)

      if (data.type === 'message') {
        setMessages(prev => [...prev, data])
        setTotalTokens(prev => prev + (data.tokens_used || 0))

        if (data.role === 'proposer') {
          setCurrentRound(prev => prev + 1)
        }
      } else if (data.type === 'debate_complete') {
        setStatus(data.status)
        setIsRunning(false)
      } else if (data.type === 'error') {
        setStatus('error')
        setIsRunning(false)
        setMessages(prev => [...prev, {
          role: 'system',
          content: `Error: ${data.message}`,
          timestamp: new Date().toISOString(),
        }])
      }
    }

    ws.onclose = () => {
      setIsRunning(false)
    }

    ws.onerror = () => {
      setStatus('error')
      setIsRunning(false)
    }
  }, [sessionId])

  const clearSession = () => {
    setMessages([])
    setSessionId(null)
    setStatus('idle')
    setCurrentRound(0)
    setTotalTokens(0)
    setDiffPreview(null)
    setApplyStatus(null)
    setValidationResult(null)
  }

  const previewDiff = async (shouldAutoApply = false) => {
    if (!sessionId) return

    setApplyStatus('loading')
    try {
      const response = await fetch(`/api/debate/${sessionId}/apply`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: sessionId,
          preview_only: true,
          validate_first: true,
          auto_apply: false,
        }),
      })

      const data = await response.json()

      if (!response.ok) {
        setDiffPreview({ error: data.detail || 'Failed to generate diff' })
        setApplyStatus('error')
        return
      }

      setDiffPreview(data)
      setValidationResult(data.validation)
      setApplyStatus(null)

      // Auto-apply if validation passed and auto-apply is enabled
      if (shouldAutoApply && data.validation?.safe_to_apply) {
        await applyCode()
      }
    } catch (error) {
      setDiffPreview({ error: error.message })
      setApplyStatus('error')
    }
  }

  const applyCode = async () => {
    if (!sessionId) return

    setApplyStatus('loading')
    try {
      const response = await fetch(`/api/debate/${sessionId}/apply`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: sessionId,
          preview_only: false,
          validate_first: true,
        }),
      })

      const data = await response.json()

      if (!response.ok) {
        setApplyStatus('error')
        alert(data.detail || 'Failed to apply code')
        return
      }

      if (data.status === 'validation_failed') {
        setApplyStatus('error')
        setValidationResult(data.validation)
        setDiffPreview({ ...data, validationFailed: true })
        return
      }

      setApplyStatus('success')
      setValidationResult(data.validation)
      setDiffPreview({ ...data, applied: true })
    } catch (error) {
      setApplyStatus('error')
      alert('Failed to apply code: ' + error.message)
    }
  }

  // Auto-validate when consensus is reached
  useEffect(() => {
    if (status === 'consensus' && sessionId && !diffPreview && autoApply) {
      previewDiff(true) // Auto-validate and apply if passes
    }
  }, [status, sessionId])

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      {/* Header */}
      <header className="border-b border-slate-800 bg-slate-900/80 backdrop-blur-sm sticky top-0 z-50">
        <div className="max-w-7xl mx-auto px-4 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-gradient-to-br from-blue-500 to-purple-600 rounded-xl">
              <Users className="w-6 h-6" />
            </div>
            <div>
              <h1 className="text-xl font-bold">LLM Debate System</h1>
              <p className="text-sm text-slate-400">Multi-LLM Consensus Engine</p>
            </div>
          </div>

          <div className="flex items-center gap-4">
            {/* Status indicators */}
            <div className="flex items-center gap-2">
              <div className={`w-2 h-2 rounded-full ${keysConfigured.anthropic ? 'bg-green-500' : 'bg-slate-600'}`} />
              <span className="text-xs text-slate-400">Claude</span>
            </div>
            <div className="flex items-center gap-2">
              <div className={`w-2 h-2 rounded-full ${keysConfigured.openai ? 'bg-green-500' : 'bg-slate-600'}`} />
              <span className="text-xs text-slate-400">GPT</span>
            </div>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto p-4">
        <div className="grid grid-cols-1 lg:grid-cols-[380px_1fr] gap-6">
          {/* Left Panel - Configuration */}
          <div className="space-y-4">
            {/* API Keys Section - Collapsed when configured */}
            <div className="bg-slate-900 rounded-xl border border-slate-800 p-4">
              <div className="flex items-center justify-between">
                <h2 className="font-semibold flex items-center gap-2">
                  <Key className="w-4 h-4 text-yellow-500" />
                  API Keys
                </h2>
                {(keysConfigured.anthropic || keysConfigured.openai) ? (
                  <button
                    onClick={() => setShowKeys(!showKeys)}
                    className="text-xs text-slate-400 hover:text-slate-200"
                  >
                    {showKeys ? 'Hide' : 'Reconfigure'}
                  </button>
                ) : null}
              </div>

              {/* Show compact status when configured and not editing */}
              {(keysConfigured.anthropic || keysConfigured.openai) && !showKeys ? (
                <div className="mt-3 flex items-center gap-4 text-sm">
                  <div className="flex items-center gap-2">
                    <CheckCircle className="w-4 h-4 text-green-500" />
                    <span className="text-slate-400">
                      {keysConfigured.anthropic && keysConfigured.openai
                        ? 'Both providers ready'
                        : keysConfigured.anthropic
                        ? 'Claude ready'
                        : 'GPT ready'}
                    </span>
                  </div>
                </div>
              ) : (
                /* Full form when not configured or reconfiguring */
                <div className="space-y-3 mt-4">
                  <div>
                    <label className="block text-xs text-slate-400 mb-1">Anthropic (Claude)</label>
                    <div className="flex gap-2">
                      <input
                        type="password"
                        value={anthropicKey}
                        onChange={e => setAnthropicKey(e.target.value)}
                        placeholder={keysConfigured.anthropic ? 'Configured - enter new to update' : 'sk-ant-...'}
                        className="flex-1 bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-blue-500"
                      />
                      {keysConfigured.anthropic && (
                        <CheckCircle className="w-5 h-5 text-green-500 my-auto" />
                      )}
                    </div>
                  </div>

                  <div>
                    <label className="block text-xs text-slate-400 mb-1">OpenAI (GPT)</label>
                    <div className="flex gap-2">
                      <input
                        type="password"
                        value={openaiKey}
                        onChange={e => setOpenaiKey(e.target.value)}
                        placeholder={keysConfigured.openai ? 'Configured - enter new to update' : 'sk-...'}
                        className="flex-1 bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-blue-500"
                      />
                      {keysConfigured.openai && (
                        <CheckCircle className="w-5 h-5 text-green-500 my-auto" />
                      )}
                    </div>
                  </div>

                  <button
                    onClick={() => {
                      configureKeys()
                      setShowKeys(false) // Collapse after saving
                    }}
                    disabled={!anthropicKey && !openaiKey}
                    className="w-full bg-blue-600 hover:bg-blue-700 disabled:bg-slate-700 disabled:text-slate-500 rounded-lg px-4 py-2 text-sm font-medium transition-colors"
                  >
                    {keysConfigured.anthropic || keysConfigured.openai ? 'Update Keys' : 'Configure Keys'}
                  </button>
                </div>
              )}
            </div>

            {/* LLM Selection */}
            <div className="bg-slate-900 rounded-xl border border-slate-800 p-4">
              <h2 className="font-semibold flex items-center gap-2 mb-4">
                <Brain className="w-4 h-4 text-purple-500" />
                LLM Configuration
              </h2>

              <div className="space-y-4">
                <div>
                  <label className="block text-xs text-slate-400 mb-2">Proposer (LLM A)</label>
                  <div className="grid grid-cols-2 gap-2">
                    {PROVIDERS.map(p => (
                      <button
                        key={p.id}
                        onClick={() => setProposerProvider(p.id)}
                        className={`px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
                          proposerProvider === p.id
                            ? 'bg-blue-600 text-white'
                            : 'bg-slate-800 text-slate-300 hover:bg-slate-700'
                        }`}
                      >
                        {p.name.split(' ')[0]}
                      </button>
                    ))}
                  </div>
                </div>

                <div>
                  <label className="block text-xs text-slate-400 mb-2">Critic (LLM B)</label>
                  <div className="grid grid-cols-2 gap-2">
                    {PROVIDERS.map(p => (
                      <button
                        key={p.id}
                        onClick={() => setCriticProvider(p.id)}
                        className={`px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
                          criticProvider === p.id
                            ? 'bg-purple-600 text-white'
                            : 'bg-slate-800 text-slate-300 hover:bg-slate-700'
                        }`}
                      >
                        {p.name.split(' ')[0]}
                      </button>
                    ))}
                  </div>
                </div>

                <div>
                  <label className="block text-xs text-slate-400 mb-2">Max Rounds: {maxRounds}</label>
                  <input
                    type="range"
                    min="3"
                    max="10"
                    value={maxRounds}
                    onChange={e => setMaxRounds(Number(e.target.value))}
                    className="w-full"
                  />
                </div>
              </div>
            </div>

            {/* Project Path */}
            <div className="bg-slate-900 rounded-xl border border-slate-800 p-4">
              <div className="flex items-center justify-between mb-4">
                <h2 className="font-semibold flex items-center gap-2">
                  <Folder className="w-4 h-4 text-amber-500" />
                  Project Path
                </h2>
                <button
                  onClick={() => configureKeys(true)}
                  disabled={!projectPath}
                  className="text-xs text-slate-400 hover:text-green-400 disabled:text-slate-600"
                  title="Save as default"
                >
                  <Save className="w-4 h-4" />
                </button>
              </div>
              <input
                type="text"
                value={projectPath}
                onChange={e => setProjectPath(e.target.value)}
                onBlur={() => projectPath && configureKeys(true)} // Auto-save on blur
                placeholder="/path/to/your/project"
                className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-blue-500"
              />
            </div>

            {/* Task Input */}
            <div className="bg-slate-900 rounded-xl border border-slate-800 p-4">
              <h2 className="font-semibold flex items-center gap-2 mb-4">
                <MessageSquare className="w-4 h-4 text-cyan-500" />
                Task / Question
              </h2>
              <textarea
                value={task}
                onChange={e => setTask(e.target.value)}
                placeholder="Describe the code change, optimization, or question you want the LLMs to debate..."
                rows={4}
                className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-blue-500 resize-none"
              />
            </div>

            {/* Action Buttons */}
            <div className="flex gap-2">
              {!isRunning ? (
                <>
                  <button
                    onClick={startDebate}
                    disabled={!keysConfigured.anthropic && !keysConfigured.openai}
                    className="flex-1 bg-gradient-to-r from-blue-600 to-purple-600 hover:from-blue-500 hover:to-purple-500 disabled:from-slate-700 disabled:to-slate-700 disabled:text-slate-500 rounded-xl px-4 py-3 font-medium flex items-center justify-center gap-2 transition-all"
                  >
                    <Play className="w-5 h-5" />
                    {sessionId && status !== 'error' ? 'Restart' : 'Start Debate'}
                  </button>

                  {/* Continue button - shows when session has error or deadlock */}
                  {sessionId && (status === 'error' || status === 'deadlock') && (
                    <button
                      onClick={() => resumeDebate()}
                      className="flex-1 bg-gradient-to-r from-green-600 to-emerald-600 hover:from-green-500 hover:to-emerald-500 rounded-xl px-4 py-3 font-medium flex items-center justify-center gap-2 transition-all"
                    >
                      <RotateCcw className="w-5 h-5" />
                      Continue
                    </button>
                  )}
                </>
              ) : (
                <button
                  onClick={stopDebate}
                  className="flex-1 bg-red-600 hover:bg-red-700 rounded-xl px-4 py-3 font-medium flex items-center justify-center gap-2"
                >
                  <XCircle className="w-5 h-5" />
                  Stop
                </button>
              )}

              <button
                onClick={clearSession}
                className="bg-slate-800 hover:bg-slate-700 rounded-xl px-4 py-3"
              >
                <RefreshCw className="w-5 h-5" />
              </button>
            </div>

            {/* Switch Provider option when error or deadlock */}
            {sessionId && (status === 'error' || status === 'deadlock') && (
              <div className={`mt-2 p-3 rounded-lg ${status === 'error' ? 'bg-red-900/20 border border-red-800/50' : 'bg-yellow-900/20 border border-yellow-800/50'}`}>
                <p className={`text-sm mb-2 ${status === 'error' ? 'text-red-400' : 'text-yellow-400'}`}>
                  {status === 'error' ? 'Debate paused due to error.' : 'Deadlock reached.'} Continue with more rounds:
                </p>
                <div className="flex gap-2">
                  <button
                    onClick={() => resumeDebate()}
                    className="text-xs bg-slate-800 hover:bg-slate-700 px-3 py-1.5 rounded-lg"
                  >
                    Same providers
                  </button>
                  <button
                    onClick={() => resumeDebate('anthropic')}
                    className="text-xs bg-orange-900/50 hover:bg-orange-800/50 px-3 py-1.5 rounded-lg text-orange-300"
                  >
                    Switch to Claude
                  </button>
                  <button
                    onClick={() => resumeDebate('openai')}
                    className="text-xs bg-green-900/50 hover:bg-green-800/50 px-3 py-1.5 rounded-lg text-green-300"
                  >
                    Switch to GPT
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* Right Panel - Debate View */}
          <div className="bg-slate-900 rounded-xl border border-slate-800 flex flex-col min-h-[700px]">
            {/* Debate Header */}
            <div className="border-b border-slate-800 p-4 flex items-center justify-between">
              <div className="flex items-center gap-4">
                <h2 className="font-semibold">Debate Session</h2>
                {sessionId && (
                  <span className="text-xs bg-slate-800 px-2 py-1 rounded">#{sessionId}</span>
                )}
              </div>

              <div className="flex items-center gap-4">
                {/* Status Badge */}
                <div className={`px-3 py-1 rounded-full text-xs font-medium ${STATUS_COLORS[status] || 'bg-slate-700'}`}>
                  {status === 'consensus' && <CheckCircle className="w-3 h-3 inline mr-1" />}
                  {status === 'deadlock' && <AlertTriangle className="w-3 h-3 inline mr-1" />}
                  {status.toUpperCase()}
                </div>

                {/* Stats */}
                <div className="flex items-center gap-3 text-xs text-slate-400">
                  <span className="flex items-center gap-1">
                    <Clock className="w-3 h-3" />
                    Round {currentRound}/{maxRounds}
                  </span>
                  <span className="flex items-center gap-1">
                    <Zap className="w-3 h-3" />
                    {totalTokens.toLocaleString()} tokens
                  </span>
                </div>
              </div>
            </div>

            {/* Messages */}
            <div className="flex-1 overflow-y-auto p-4 space-y-4">
              {messages.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-full text-slate-500">
                  <Users className="w-12 h-12 mb-4 opacity-50" />
                  <p>Configure settings and start a debate</p>
                  <p className="text-sm mt-2">LLMs will discuss and reach consensus</p>
                </div>
              ) : (
                messages.map((msg, idx) => {
                  const style = ROLE_STYLES[msg.role] || ROLE_STYLES.system
                  const Icon = style.icon

                  return (
                    <div
                      key={idx}
                      className={`message-enter rounded-xl border p-4 ${style.bg} ${style.border}`}
                    >
                      {/* Message Header */}
                      <div className="flex items-center justify-between mb-3">
                        <div className="flex items-center gap-2">
                          <Icon className={`w-4 h-4 ${style.labelColor}`} />
                          <span className={`text-xs font-bold ${style.labelColor}`}>
                            {style.label}
                          </span>
                          {msg.provider !== 'system' && (
                            <span className="text-xs text-slate-500">
                              {msg.model}
                            </span>
                          )}
                        </div>

                        <div className="flex items-center gap-2">
                          {msg.is_consensus && (
                            <span className="text-xs bg-green-600 px-2 py-0.5 rounded-full">
                              APPROVED
                            </span>
                          )}
                          {msg.tokens_used > 0 && (
                            <span className="text-xs text-slate-500">
                              {msg.tokens_used} tokens
                            </span>
                          )}
                        </div>
                      </div>

                      {/* Message Content */}
                      <div className="prose prose-invert prose-sm max-w-none">
                        <ReactMarkdown>{msg.content}</ReactMarkdown>
                      </div>

                      {/* Concerns */}
                      {msg.concerns && msg.concerns.length > 0 && (
                        <div className="mt-3 pt-3 border-t border-slate-700/50">
                          <div className="text-xs font-semibold text-red-400 mb-2">Concerns Raised:</div>
                          <ul className="text-xs text-slate-400 space-y-1">
                            {msg.concerns.map((c, i) => (
                              <li key={i}>{c}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                    </div>
                  )
                })
              )}

              {/* Typing Indicator */}
              {isRunning && (
                <div className="flex items-center gap-2 text-slate-500 typing-indicator">
                  <div className="flex gap-1">
                    <div className="w-2 h-2 bg-slate-500 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                    <div className="w-2 h-2 bg-slate-500 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                    <div className="w-2 h-2 bg-slate-500 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
                  </div>
                  <span className="text-sm">LLMs are debating...</span>
                </div>
              )}

              <div ref={messagesEndRef} />
            </div>

            {/* Consensus Banner with Apply Button */}
            {status === 'consensus' && (
              <div className="border-t border-green-800 bg-green-900/30 p-4">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <CheckCircle className="w-6 h-6 text-green-500" />
                    <div>
                      <div className="font-semibold text-green-400">Consensus Reached!</div>
                      <div className="text-sm text-slate-400">
                        Both LLMs agreed on the solution after {currentRound} round(s)
                      </div>
                    </div>
                  </div>

                  {/* Apply Code Buttons */}
                  <div className="flex items-center gap-3">
                    {/* Auto-apply toggle */}
                    <label className="flex items-center gap-2 text-xs text-slate-400 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={autoApply}
                        onChange={e => setAutoApply(e.target.checked)}
                        className="rounded bg-slate-700 border-slate-600"
                      />
                      Auto-apply
                    </label>

                    {/* Validation status */}
                    {validationResult && (
                      <span className={`text-xs px-2 py-1 rounded ${
                        validationResult.safe_to_apply
                          ? 'bg-green-900/50 text-green-400'
                          : 'bg-red-900/50 text-red-400'
                      }`}>
                        {validationResult.safe_to_apply ? 'Validated' : 'Validation Failed'}
                      </span>
                    )}

                    {!diffPreview && (
                      <button
                        onClick={() => previewDiff(false)}
                        disabled={applyStatus === 'loading'}
                        className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-slate-700 rounded-lg text-sm font-medium"
                      >
                        <Code className="w-4 h-4" />
                        {applyStatus === 'loading' ? 'Validating...' : 'Preview Diff'}
                      </button>
                    )}
                    {diffPreview && !diffPreview.error && !diffPreview.applied && !diffPreview.validationFailed && (
                      <button
                        onClick={applyCode}
                        disabled={applyStatus === 'loading'}
                        className="flex items-center gap-2 px-4 py-2 bg-green-600 hover:bg-green-700 disabled:bg-slate-700 rounded-lg text-sm font-medium"
                      >
                        <Save className="w-4 h-4" />
                        {applyStatus === 'loading' ? 'Applying...' : 'Apply to File'}
                      </button>
                    )}
                    {diffPreview?.validationFailed && (
                      <span className="flex items-center gap-2 px-4 py-2 bg-red-800 rounded-lg text-sm font-medium text-red-300">
                        <XCircle className="w-4 h-4" />
                        Blocked
                      </span>
                    )}
                    {diffPreview?.applied && (
                      <span className="flex items-center gap-2 px-4 py-2 bg-green-800 rounded-lg text-sm font-medium text-green-300">
                        <CheckCircle className="w-4 h-4" />
                        Applied!
                      </span>
                    )}
                  </div>
                </div>

                {/* Validation Details */}
                {validationResult && (
                  <div className={`mt-4 p-3 rounded-lg border ${
                    validationResult.safe_to_apply
                      ? 'bg-green-900/20 border-green-800/50'
                      : 'bg-red-900/20 border-red-800/50'
                  }`}>
                    <div className="flex items-center gap-2 text-sm font-medium mb-2">
                      {validationResult.safe_to_apply ? (
                        <>
                          <CheckCircle className="w-4 h-4 text-green-500" />
                          <span className="text-green-400">Dry-run Validation Passed</span>
                        </>
                      ) : (
                        <>
                          <XCircle className="w-4 h-4 text-red-500" />
                          <span className="text-red-400">Validation Failed - Apply Blocked</span>
                        </>
                      )}
                    </div>
                    <div className="text-xs text-slate-400 space-y-1">
                      <div>Syntax: {validationResult.syntax_valid ? 'Valid' : 'Invalid'}</div>
                      {validationResult.tests_skipped ? (
                        <div>Tests: Skipped</div>
                      ) : (
                        <div>Tests: {validationResult.tests_passed ? 'Passed' : 'Failed'}</div>
                      )}
                      {validationResult.errors?.length > 0 && (
                        <div className="text-red-400 mt-2">
                          Errors: {validationResult.errors.join('; ')}
                        </div>
                      )}
                      {validationResult.warnings?.length > 0 && (
                        <div className="text-yellow-400">
                          Warnings: {validationResult.warnings.join('; ')}
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {/* Diff Preview */}
                {diffPreview && (
                  <div className="mt-4 rounded-lg bg-slate-950 border border-slate-800 overflow-hidden">
                    <div className="flex items-center justify-between px-4 py-2 bg-slate-900 border-b border-slate-800">
                      <div className="flex items-center gap-2 text-sm">
                        <FileCode className="w-4 h-4 text-blue-400" />
                        <span className="text-slate-300">{diffPreview.file || 'Unknown file'}</span>
                        {diffPreview.is_new_file && (
                          <span className="text-xs bg-blue-600 px-2 py-0.5 rounded">NEW FILE</span>
                        )}
                      </div>
                      {diffPreview.old_lines && (
                        <span className="text-xs text-slate-500">
                          {diffPreview.old_lines} → {diffPreview.new_lines} lines
                        </span>
                      )}
                    </div>

                    {diffPreview.error ? (
                      <div className="p-4 text-red-400 text-sm">{diffPreview.error}</div>
                    ) : diffPreview.diff ? (
                      <pre className="p-4 text-xs overflow-x-auto max-h-80 overflow-y-auto font-mono">
                        {diffPreview.diff.split('\n').map((line, i) => (
                          <div
                            key={i}
                            className={
                              line.startsWith('+') && !line.startsWith('+++')
                                ? 'text-green-400 bg-green-900/20'
                                : line.startsWith('-') && !line.startsWith('---')
                                ? 'text-red-400 bg-red-900/20'
                                : line.startsWith('@@')
                                ? 'text-blue-400'
                                : 'text-slate-400'
                            }
                          >
                            {line}
                          </div>
                        ))}
                      </pre>
                    ) : diffPreview.new_code ? (
                      <pre className="p-4 text-xs overflow-x-auto max-h-80 overflow-y-auto font-mono text-green-400">
                        {diffPreview.new_code}
                      </pre>
                    ) : null}
                  </div>
                )}
              </div>
            )}

            {status === 'deadlock' && (
              <div className="border-t border-yellow-800 bg-yellow-900/30 p-4">
                <div className="flex items-center gap-3">
                  <AlertTriangle className="w-6 h-6 text-yellow-500" />
                  <div>
                    <div className="font-semibold text-yellow-400">Deadlock Reached</div>
                    <div className="text-sm text-slate-400">
                      Max rounds reached without full consensus. Review the last proposal manually.
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      </main>
    </div>
  )
}

export default App
