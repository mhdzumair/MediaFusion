import { useEffect, useRef, useState } from 'react'

/**
 * Maintains a long-lived SSE connection. Uses a stable `streamKey` (serialized params)
 * so filter changes reconnect, but callback identity changes do not.
 */
export function useEventStreamConnection(options: {
  enabled: boolean
  streamKey: string
  reconnectMs?: number
  connect: (signal: AbortSignal, onConnected: () => void) => Promise<void>
}) {
  const [isConnected, setIsConnected] = useState(false)
  const [lastEventAt, setLastEventAt] = useState<string | null>(null)
  const connectRef = useRef(options.connect)
  const onConnectedRef = useRef<() => void>(() => {})

  connectRef.current = options.connect
  onConnectedRef.current = () => {
    setIsConnected(true)
    setLastEventAt(new Date().toISOString())
  }

  useEffect(() => {
    if (!options.enabled) {
      setIsConnected(false)
      return
    }

    const controller = new AbortController()
    let reconnectTimer: number | undefined

    const connect = async () => {
      try {
        setIsConnected(false)
        await connectRef.current(controller.signal, onConnectedRef.current)
      } catch {
        if (controller.signal.aborted) {
          return
        }
      } finally {
        if (!controller.signal.aborted) {
          reconnectTimer = window.setTimeout(connect, options.reconnectMs ?? 2000)
        }
      }
    }

    connect()

    return () => {
      controller.abort()
      setIsConnected(false)
      if (reconnectTimer !== undefined) {
        window.clearTimeout(reconnectTimer)
      }
    }
  }, [options.enabled, options.reconnectMs, options.streamKey])

  return { isConnected, lastEventAt }
}
