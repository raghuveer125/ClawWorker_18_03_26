import { useEffect, useState, useRef } from 'react'
import { IS_STATIC } from '../api'

export const useWebSocket = () => {
  const [lastMessage, setLastMessage]       = useState(null)
  const [connectionStatus, setConnectionStatus] = useState(IS_STATIC ? 'github-pages' : 'connecting')
  const ws = useRef(null)
  const reconnectTimer = useRef(null)

  useEffect(() => {
    // No WebSocket on GitHub Pages — it's a static deployment
    if (IS_STATIC) return

    let disposed = false

    const connectWebSocket = () => {
      if (disposed) return

      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const wsUrl = `${protocol}//${window.location.hostname}:${window.location.port}/ws`

      ws.current = new WebSocket(wsUrl)

      ws.current.onopen = () => {
        if (disposed) {
          ws.current?.close()
          return
        }
        setConnectionStatus('connected')
      }

      ws.current.onmessage = (event) => {
        if (disposed) return
        try {
          setLastMessage(JSON.parse(event.data))
        } catch {}
      }

      ws.current.onerror = () => {
        if (disposed) return
        setConnectionStatus('error')
      }

      ws.current.onclose = () => {
        if (disposed) return
        setConnectionStatus('disconnected')
        reconnectTimer.current = setTimeout(connectWebSocket, 3000)
      }
    }

    connectWebSocket()

    return () => {
      disposed = true
      if (reconnectTimer.current) {
        clearTimeout(reconnectTimer.current)
        reconnectTimer.current = null
      }

      // Avoid noisy "closed before established" warning during StrictMode cleanup.
      // If still CONNECTING, let it settle; handlers are guarded by `disposed`.
      if (ws.current && ws.current.readyState === WebSocket.OPEN) {
        ws.current.close()
      }
    }
  }, [])

  return { lastMessage, connectionStatus }
}
