import { useEffect, useRef, useState } from 'react'
import { IS_STATIC, resolveWebSocketUrl } from '../api'

export const useMarketWebSocket = (enabled = false) => {
  const [lastMessage, setLastMessage] = useState(null)
  const [connectionStatus, setConnectionStatus] = useState(
    IS_STATIC ? 'github-pages' : (enabled ? 'connecting' : 'idle'),
  )
  const ws = useRef(null)
  const reconnectTimer = useRef(null)

  useEffect(() => {
    if (IS_STATIC) return

    if (!enabled) {
      setConnectionStatus('idle')
      if (reconnectTimer.current) {
        clearTimeout(reconnectTimer.current)
        reconnectTimer.current = null
      }
      if (ws.current && ws.current.readyState === WebSocket.OPEN) {
        ws.current.close()
      }
      return
    }

    let disposed = false

    const connectWebSocket = () => {
      if (disposed) return

      const wsUrl = resolveWebSocketUrl('/ws/market')
      setConnectionStatus('connecting')
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
      if (ws.current && ws.current.readyState === WebSocket.OPEN) {
        ws.current.close()
      }
    }
  }, [enabled])

  return { lastMessage, connectionStatus }
}
