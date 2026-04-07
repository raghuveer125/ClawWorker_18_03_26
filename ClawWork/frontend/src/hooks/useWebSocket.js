import { useEffect, useState, useRef } from 'react'
import { IS_STATIC, resolveWebSocketUrl } from '../api'

const RECONNECT_DELAY_MS = 3000
const CLOSE_GRACE_MS = 1500

let sharedSocket = null
let sharedReconnectTimer = null
let sharedCloseTimer = null
let sharedConnectPromise = null
let sharedLastMessage = null
let sharedConnectionStatus = IS_STATIC ? 'github-pages' : 'disconnected'
const subscribers = new Set()

const notifySubscribers = () => {
  const snapshot = {
    lastMessage: sharedLastMessage,
    connectionStatus: sharedConnectionStatus,
  }

  subscribers.forEach((subscriber) => subscriber(snapshot))
}

const clearReconnectTimer = () => {
  if (sharedReconnectTimer) {
    clearTimeout(sharedReconnectTimer)
    sharedReconnectTimer = null
  }
}

const clearCloseTimer = () => {
  if (sharedCloseTimer) {
    clearTimeout(sharedCloseTimer)
    sharedCloseTimer = null
  }
}

const scheduleReconnect = () => {
  if (IS_STATIC || sharedReconnectTimer || subscribers.size === 0) return

  sharedReconnectTimer = setTimeout(() => {
    sharedReconnectTimer = null
    void ensureSharedConnection()
  }, RECONNECT_DELAY_MS)
}

const closeSharedSocket = () => {
  const socket = sharedSocket
  sharedSocket = null

  if (socket && (socket.readyState === WebSocket.CONNECTING || socket.readyState === WebSocket.OPEN)) {
    socket.close()
  }
}

const isBackendReady = async () => {
  try {
    const response = await fetch('/api/', { cache: 'no-store' })
    return response.ok
  } catch {
    return false
  }
}

const ensureSharedConnection = async () => {
  if (IS_STATIC || sharedSocket || sharedConnectPromise || subscribers.size === 0) return

  sharedConnectionStatus = 'connecting'
  notifySubscribers()

  sharedConnectPromise = (async () => {
    const backendReady = await isBackendReady()
    if (!backendReady) {
      sharedConnectionStatus = 'disconnected'
      notifySubscribers()
      scheduleReconnect()
      return
    }

    const socket = new WebSocket(resolveWebSocketUrl('/ws'))
    sharedSocket = socket

    socket.onopen = () => {
      if (sharedSocket !== socket) {
        socket.close()
        return
      }
      sharedConnectionStatus = 'connected'
      notifySubscribers()
    }

    socket.onmessage = (event) => {
      if (sharedSocket !== socket) return
      try {
        sharedLastMessage = JSON.parse(event.data)
        notifySubscribers()
      } catch {}
    }

    socket.onerror = () => {
      if (sharedSocket !== socket) return
      sharedConnectionStatus = 'error'
      notifySubscribers()
    }

    socket.onclose = () => {
      if (sharedSocket === socket) {
        sharedSocket = null
      }

      if (IS_STATIC) return

      sharedConnectionStatus = 'disconnected'
      notifySubscribers()
      scheduleReconnect()
    }
  })().finally(() => {
    sharedConnectPromise = null
  })

  return sharedConnectPromise
}

export const useWebSocket = () => {
  const [lastMessage, setLastMessage]       = useState(null)
  const [connectionStatus, setConnectionStatus] = useState(IS_STATIC ? 'github-pages' : sharedConnectionStatus)
  const subscriberRef = useRef(null)

  useEffect(() => {
    // No WebSocket on GitHub Pages — it's a static deployment
    if (IS_STATIC) return

    const subscriber = ({ lastMessage: nextMessage, connectionStatus: nextStatus }) => {
      setLastMessage(nextMessage)
      setConnectionStatus(nextStatus)
    }

    subscriberRef.current = subscriber
    subscribers.add(subscriber)
    clearCloseTimer()
    subscriber({
      lastMessage: sharedLastMessage,
      connectionStatus: sharedConnectionStatus,
    })
    void ensureSharedConnection()

    return () => {
      if (subscriberRef.current) {
        subscribers.delete(subscriberRef.current)
        subscriberRef.current = null
      }

      if (subscribers.size === 0) {
        clearReconnectTimer()
        sharedCloseTimer = setTimeout(() => {
          sharedCloseTimer = null
          if (subscribers.size === 0) {
            closeSharedSocket()
            if (!IS_STATIC) {
              sharedConnectionStatus = 'disconnected'
              notifySubscribers()
            }
          }
        }, CLOSE_GRACE_MS)
      }
    }
  }, [])

  return { lastMessage, connectionStatus }
}
