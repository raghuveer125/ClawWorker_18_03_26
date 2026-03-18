import { useState, useEffect, useMemo, useCallback } from 'react'
import { Brain, BookOpen, Sparkles, Clock, AlertCircle, Search, X } from 'lucide-react'
import { motion, AnimatePresence } from 'framer-motion'
import { format } from 'date-fns'
import { fetchAgentLearning, IS_STATIC } from '../api'
import { useDisplayName } from '../DisplayNamesContext'

const LearningView = ({ agents, selectedAgent, lastMessage, connectionStatus }) => {
  const dn = useDisplayName()
  const [learningData, setLearningData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [selectedEntry, setSelectedEntry] = useState(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [isDocumentVisible, setIsDocumentVisible] = useState(() => {
    if (typeof document === 'undefined') return true
    return document.visibilityState !== 'hidden'
  })

  // Define entries and filtered entries at the top (hooks must be called in same order every render)
  const entries = learningData?.entries || []
  const filteredEntries = useMemo(() => {
    if (!searchQuery.trim()) return entries
    const query = searchQuery.toLowerCase()
    return entries.filter(entry =>
      entry.topic?.toLowerCase().includes(query) ||
      entry.content?.toLowerCase().includes(query)
    )
  }, [entries, searchQuery])

  const fetchLearningData = useCallback(async (signature) => {
    try {
      setLoading(true)
      const data = await fetchAgentLearning(signature)
      setLearningData(data)
    } catch (error) {
      console.error('Error fetching learning data:', error)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!selectedAgent) {
      setLearningData(null)
      setSelectedEntry(null)
      setLoading(false)
      return
    }

    setLearningData(null)
    setSelectedEntry(null)
    fetchLearningData(selectedAgent)
  }, [fetchLearningData, selectedAgent])

  useEffect(() => {
    if (!selectedAgent || !lastMessage || typeof lastMessage !== 'object') return

    if (lastMessage.type === 'connected') {
      fetchLearningData(selectedAgent)
      return
    }

    if (lastMessage.type === 'learning_update' && lastMessage.signature === selectedAgent) {
      fetchLearningData(selectedAgent)
    }
  }, [fetchLearningData, lastMessage, selectedAgent])

  useEffect(() => {
    if (typeof document === 'undefined') return undefined

    const handleVisibilityChange = () => {
      setIsDocumentVisible(document.visibilityState !== 'hidden')
    }

    document.addEventListener('visibilitychange', handleVisibilityChange)
    return () => document.removeEventListener('visibilitychange', handleVisibilityChange)
  }, [])

  useEffect(() => {
    if (!selectedAgent || IS_STATIC || !isDocumentVisible || connectionStatus === 'connected') return

    const interval = setInterval(() => {
      fetchLearningData(selectedAgent)
    }, 30000)

    return () => clearInterval(interval)
  }, [connectionStatus, fetchLearningData, isDocumentVisible, selectedAgent])

  if (!selectedAgent) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center">
          <AlertCircle className="w-16 h-16 text-gray-400 mx-auto mb-4" />
          <h2 className="text-2xl font-bold text-gray-600">No Agent Selected</h2>
          <p className="text-gray-500 mt-2">Select an agent from the sidebar to view learning</p>
        </div>
      </div>
    )
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-primary-600"></div>
      </div>
    )
  }

  return (
    <div className="p-8 space-y-6">
      {/* Header */}
      <motion.div
        initial={{ opacity: 0, y: -20 }}
        animate={{ opacity: 1, y: 0 }}
        className="flex items-center justify-between"
      >
        <div>
          <h1 className="text-3xl font-bold text-gray-900">Learning & Knowledge</h1>
          <p className="text-gray-500 mt-1">Agent's accumulated knowledge and insights</p>
          <p className="text-sm text-gray-400 mt-1">Selected agent: {dn(selectedAgent)}</p>
        </div>
        <div className="flex items-center space-x-4">
          <div className="bg-white rounded-xl px-6 py-3 shadow-sm border border-gray-200">
            <p className="text-sm text-gray-500">Learning Entries</p>
            <p className="text-2xl font-bold text-gray-900">{entries.length}</p>
          </div>
        </div>
      </motion.div>

      {/* Learning Stats Banner */}
      <motion.div
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        className="bg-gradient-to-r from-purple-500 via-pink-500 to-red-500 rounded-2xl p-6 text-white shadow-lg"
      >
        <div className="flex items-center space-x-4">
          <div className="w-16 h-16 bg-white/20 rounded-2xl flex items-center justify-center animate-pulse-slow">
            <Brain className="w-8 h-8" />
          </div>
          <div>
            <p className="text-sm font-medium opacity-90 mb-1">Knowledge Base</p>
            <p className="text-3xl font-bold">
              {entries.length} Topics Learned
            </p>
            <p className="text-sm opacity-75 mt-1">
              Building knowledge for better performance
            </p>
          </div>
        </div>
      </motion.div>

      {/* Search Filter */}
      {entries.length > 0 && (
        <motion.div
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          className="relative"
        >
          <div className="relative">
            <Search className="absolute left-4 top-1/2 transform -translate-y-1/2 w-5 h-5 text-gray-400" />
            <input
              type="text"
              placeholder="Search topics or content..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full pl-12 pr-12 py-3 bg-white border border-gray-200 rounded-xl shadow-sm focus:outline-none focus:ring-2 focus:ring-purple-500 focus:border-transparent transition-all"
            />
            {searchQuery && (
              <button
                onClick={() => setSearchQuery('')}
                className="absolute right-4 top-1/2 transform -translate-y-1/2 text-gray-400 hover:text-gray-600"
              >
                <X className="w-5 h-5" />
              </button>
            )}
          </div>
          {searchQuery && (
            <p className="mt-2 text-sm text-gray-500">
              Found {filteredEntries.length} of {entries.length} entries matching "{searchQuery}"
            </p>
          )}
        </motion.div>
      )}

      {/* Learning Timeline */}
      <div className="space-y-4">
        <h2 className="text-xl font-semibold text-gray-900 flex items-center space-x-2">
          <Sparkles className="w-5 h-5 text-yellow-500" />
          <span>Learning Timeline</span>
          {searchQuery && filteredEntries.length !== entries.length && (
            <span className="text-sm font-normal text-gray-500">
              (filtered)
            </span>
          )}
        </h2>

        <div className="space-y-4">
          <AnimatePresence>
            {filteredEntries.map((entry, index) => (
              <motion.div
                key={`${entry.topic}-${entry.timestamp}-${index}`}
                initial={{ opacity: 0, x: -20 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: 20 }}
                transition={{ delay: index * 0.05 }}
                className="relative"
              >
                {/* Timeline Line */}
                {index < filteredEntries.length - 1 && (
                  <div className="absolute left-6 top-16 w-0.5 h-full bg-gradient-to-b from-purple-200 to-transparent z-0"></div>
                )}

                {/* Entry Card */}
                <div
                  className="bg-white rounded-2xl p-6 shadow-sm border border-gray-200 hover:shadow-md transition-all cursor-pointer relative z-10"
                  onClick={() => setSelectedEntry(entry)}
                >
                  <div className="flex items-start space-x-4">
                    {/* Icon */}
                    <div className="w-12 h-12 bg-gradient-to-br from-purple-100 to-pink-100 rounded-xl flex items-center justify-center flex-shrink-0">
                      <BookOpen className="w-6 h-6 text-purple-600" />
                    </div>

                    {/* Content */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center space-x-3 mb-2">
                        <h3 className="text-lg font-semibold text-gray-900">
                          {entry.topic}
                        </h3>
                        <span className="px-3 py-1 bg-purple-50 text-purple-700 rounded-full text-xs font-medium border border-purple-200">
                          New Learning
                        </span>
                      </div>

                      {entry.timestamp && (
                        <div className="flex items-center space-x-2 mb-3">
                          <Clock className="w-4 h-4 text-gray-400" />
                          <span className="text-sm text-gray-500">{entry.timestamp}</span>
                        </div>
                      )}

                      <p className="text-sm text-gray-600 line-clamp-3">
                        {entry.content}
                      </p>

                      <button
                        onClick={(e) => {
                          e.stopPropagation()
                          setSelectedEntry(entry)
                        }}
                        className="mt-3 text-sm text-primary-600 hover:text-primary-700 font-medium"
                      >
                        Read more →
                      </button>
                    </div>

                    {/* Badge */}
                    <div className="flex-shrink-0">
                      <div className="w-8 h-8 bg-gradient-to-br from-yellow-100 to-orange-100 rounded-lg flex items-center justify-center">
                        <Sparkles className="w-4 h-4 text-yellow-600" />
                      </div>
                    </div>
                  </div>
                </div>
              </motion.div>
            ))}
          </AnimatePresence>

          {filteredEntries.length === 0 && entries.length > 0 && (
            <div className="text-center py-12">
              <Search className="w-16 h-16 text-gray-300 mx-auto mb-4" />
              <h3 className="text-lg font-medium text-gray-600">No results found for "{searchQuery}"</h3>
              <p className="text-gray-500 mt-2">
                Try a different search term
              </p>
              <button
                onClick={() => setSearchQuery('')}
                className="mt-4 px-4 py-2 bg-purple-100 text-purple-700 rounded-lg hover:bg-purple-200 transition-colors"
              >
                Clear search
              </button>
            </div>
          )}

          {entries.length === 0 && (
            <div className="text-center py-12">
              <Brain className="w-16 h-16 text-gray-300 mx-auto mb-4" />
              <h3 className="text-lg font-medium text-gray-600">No learning yet for {dn(selectedAgent)}</h3>
              <p className="text-gray-500 mt-2">
                Agent will accumulate knowledge here as they learn
              </p>
            </div>
          )}
        </div>
      </div>

      {/* Learning Entry Detail Modal */}
      <AnimatePresence>
        {selectedEntry && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 bg-black/50 flex items-center justify-center p-4 z-50"
            onClick={() => setSelectedEntry(null)}
          >
            <motion.div
              initial={{ scale: 0.9, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.9, opacity: 0 }}
              className="bg-white rounded-2xl max-w-3xl w-full max-h-[80vh] overflow-y-auto"
              onClick={(e) => e.stopPropagation()}
            >
              {/* Header */}
              <div className="bg-gradient-to-r from-purple-500 to-pink-500 p-8 text-white rounded-t-2xl">
                <div className="flex items-start justify-between mb-4">
                  <div className="w-14 h-14 bg-white/20 rounded-2xl flex items-center justify-center">
                    <Brain className="w-8 h-8" />
                  </div>
                  <button
                    onClick={() => setSelectedEntry(null)}
                    className="text-white/80 hover:text-white transition-colors text-2xl"
                  >
                    ✕
                  </button>
                </div>
                <h2 className="text-3xl font-bold mb-2">{selectedEntry.topic}</h2>
                {selectedEntry.timestamp && (
                  <div className="flex items-center space-x-2 text-white/80">
                    <Clock className="w-4 h-4" />
                    <span className="text-sm">{selectedEntry.timestamp}</span>
                  </div>
                )}
              </div>

              {/* Content */}
              <div className="p-8">
                <div className="prose max-w-none">
                  <div className="text-gray-700 whitespace-pre-wrap leading-relaxed">
                    {selectedEntry.content}
                  </div>
                </div>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

export default LearningView
