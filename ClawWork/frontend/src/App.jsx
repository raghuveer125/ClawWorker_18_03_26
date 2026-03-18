import { lazy, Suspense, useState, useEffect, useRef, useCallback } from 'react'
import { BrowserRouter as Router, Routes, Route } from 'react-router-dom'
import Sidebar from './components/Sidebar'
import PasswordGate from './components/PasswordGate'
import { useWebSocket } from './hooks/useWebSocket'
import { fetchAgents, fetchHiddenAgents, saveHiddenAgents, fetchDisplayNames, IS_STATIC } from './api'
import { DisplayNamesContext } from './DisplayNamesContext'

const Dashboard = lazy(() => import('./pages/Dashboard'))
const AgentDetail = lazy(() => import('./pages/AgentDetail'))
const WorkView = lazy(() => import('./pages/WorkView'))
const LearningView = lazy(() => import('./pages/LearningView'))
const Leaderboard = lazy(() => import('./pages/Leaderboard'))
const Artifacts = lazy(() => import('./pages/Artifacts'))
const BotEnsemble = lazy(() => import('./pages/BotEnsemble'))
const SignalView = lazy(() => import('./pages/SignalView'))
const SwingAnalysis = lazy(() => import('./pages/SwingAnalysis'))
const ScalpingDashboard = lazy(() => import('./pages/ScalpingDashboard'))

function App() {
  const [agents, setAgents] = useState([])
  const [selectedAgent, setSelectedAgent] = useState(null)
  const [selectionEpoch, setSelectionEpoch] = useState(0)
  const [hiddenAgents, setHiddenAgents] = useState(new Set())
  const [displayNames, setDisplayNames] = useState({})
  const [sidebarPinned, setSidebarPinned] = useState(() => {
    // Default to unpinned (hidden) on mobile
    if (typeof window !== 'undefined' && window.innerWidth < 768) return false
    return localStorage.getItem('sidebarPinned') !== 'false'
  })
  const [isDocumentVisible, setIsDocumentVisible] = useState(() => {
    if (typeof document === 'undefined') return true
    return document.visibilityState !== 'hidden'
  })
  const { lastMessage, connectionStatus } = useWebSocket()
  const hasAutoSelected = useRef(false)

  const fetchAgentsData = useCallback(async () => {
    try {
      const data = await fetchAgents()
      setAgents(data.agents || [])
    } catch (error) {
      console.error('Error fetching agents:', error)
    }
  }, [])

  const handleWebSocketMessage = useCallback((message) => {
    if (!message || typeof message !== 'object') return

    if (
      message.type === 'connected' ||
      message.type === 'balance_update' ||
      message.type === 'activity_update'
    ) {
      fetchAgentsData()
    }
  }, [fetchAgentsData])

  // Save sidebar state to localStorage
  useEffect(() => {
    localStorage.setItem('sidebarPinned', sidebarPinned.toString())
  }, [sidebarPinned])

  // Auto-select first VISIBLE agent once both agents and hiddenAgents are loaded
  useEffect(() => {
    if (hasAutoSelected.current) return
    const firstVisible = agents.find(a => !hiddenAgents.has(a.signature))
    if (firstVisible) {
      setSelectedAgent(firstVisible.signature)
      hasAutoSelected.current = true
    }
  }, [agents, hiddenAgents])

  // Fetch hidden agents on mount
  useEffect(() => {
    fetchHiddenAgents()
      .then(data => setHiddenAgents(new Set(data.hidden || [])))
      .catch(err => console.error('Error fetching hidden agents:', err))
  }, [])

  // Fetch display names on mount
  useEffect(() => {
    fetchDisplayNames()
      .then(data => setDisplayNames(data || {}))
      .catch(() => {})
  }, [])

  // Fetch agents once on mount.
  useEffect(() => {
    fetchAgentsData()
  }, [fetchAgentsData])

  useEffect(() => {
    if (typeof document === 'undefined') return undefined

    const handleVisibilityChange = () => {
      setIsDocumentVisible(document.visibilityState !== 'hidden')
    }

    document.addEventListener('visibilitychange', handleVisibilityChange)
    return () => document.removeEventListener('visibilitychange', handleVisibilityChange)
  }, [])

  // Fall back to timed polling only when websocket updates are unavailable.
  useEffect(() => {
    if (IS_STATIC || !isDocumentVisible || connectionStatus === 'connected') return

    const interval = setInterval(fetchAgentsData, 30000)
    return () => clearInterval(interval)
  }, [connectionStatus, fetchAgentsData, isDocumentVisible])

  // Handle WebSocket messages
  useEffect(() => {
    if (lastMessage) handleWebSocketMessage(lastMessage)
  }, [handleWebSocketMessage, lastMessage])

  const updateHiddenAgents = useCallback(async (newHiddenSet) => {
    setHiddenAgents(newHiddenSet)
    try {
      await saveHiddenAgents(Array.from(newHiddenSet))
    } catch (error) {
      console.error('Error saving hidden agents:', error)
    }
  }, [])

  const handleSelectAgent = useCallback((signature) => {
    setSelectionEpoch(prev => prev + 1)
    setSelectedAgent(signature)
  }, [])

  const visibleAgents = agents.filter(a => !hiddenAgents.has(a.signature))

  return (
    <PasswordGate>
    <DisplayNamesContext.Provider value={displayNames}>
    <Router
      basename={import.meta.env.BASE_URL}
      future={{
        v7_startTransition: true,
        v7_relativeSplatPath: true,
      }}
    >
      <div className="flex h-screen bg-gray-50">
        <Sidebar
          agents={visibleAgents}
          allAgents={agents}
          hiddenAgents={hiddenAgents}
          onUpdateHiddenAgents={updateHiddenAgents}
          selectedAgent={selectedAgent}
          onSelectAgent={handleSelectAgent}
          connectionStatus={connectionStatus}
          isPinned={sidebarPinned}
          onTogglePin={() => setSidebarPinned(prev => !prev)}
        />

        <main className="flex-1 overflow-y-auto">
          <Suspense fallback={<div className="p-6 text-slate-400">Loading view...</div>}>
            <Routes>
              <Route path="/" element={
                <Leaderboard
                  hiddenAgents={hiddenAgents}
                  lastMessage={lastMessage}
                  connectionStatus={connectionStatus}
                />
              } />
              <Route path="/dashboard" element={
                <Dashboard
                  key={`dashboard-${selectedAgent || 'none'}-${selectionEpoch}`}
                  agents={visibleAgents}
                  selectedAgent={selectedAgent}
                />
              } />
              <Route path="/agent/:signature" element={
                <AgentDetail onRouteAgentSelected={handleSelectAgent} />
              } />
              <Route path="/artifacts" element={
                <Artifacts />
              } />
              <Route path="/work" element={
                <WorkView
                  key={`work-${selectedAgent || 'none'}-${selectionEpoch}`}
                  agents={visibleAgents}
                  selectedAgent={selectedAgent}
                />
              } />
              <Route path="/learning" element={
                <LearningView
                  key={`learning-${selectedAgent || 'none'}-${selectionEpoch}`}
                  agents={visibleAgents}
                  selectedAgent={selectedAgent}
                  lastMessage={lastMessage}
                  connectionStatus={connectionStatus}
                />
              } />
              <Route path="/bots" element={
                <BotEnsemble />
              } />
              <Route path="/signals" element={
                <SignalView />
              } />
              <Route path="/swing-analysis" element={
                <SwingAnalysis />
              } />
              <Route path="/scalping" element={
                <ScalpingDashboard />
              } />
            </Routes>
          </Suspense>
        </main>
      </div>
    </Router>
    </DisplayNamesContext.Provider>
    </PasswordGate>
  )
}

export default App
