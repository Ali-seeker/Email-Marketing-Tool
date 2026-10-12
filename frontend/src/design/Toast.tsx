import React, { createContext, useContext, useState, useCallback } from 'react'
import { CheckCircle2, AlertTriangle, AlertCircle, Info, X } from 'lucide-react'
import { cn } from '@/lib/utils'

export type ToastType = 'success' | 'warning' | 'error' | 'info'

export interface ToastItem {
  id: string
  title: string
  message?: string
  type: ToastType
  duration?: number
}

interface ToastContextType {
  toast: (item: Omit<ToastItem, 'id'>) => void
  success: (title: string, message?: string) => void
  error: (title: string, message?: string) => void
  warning: (title: string, message?: string) => void
  info: (title: string, message?: string) => void
  dismiss: (id: string) => void
}

const ToastContext = createContext<ToastContextType | undefined>(undefined)

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([])

  const dismiss = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id))
  }, [])

  const toast = useCallback(
    ({ title, message, type = 'info', duration = 4000 }: Omit<ToastItem, 'id'>) => {
      const id = `${Date.now()}-${Math.random()}`
      const newToast: ToastItem = { id, title, message, type, duration }
      setToasts((prev) => [...prev, newToast])

      if (duration > 0) {
        setTimeout(() => {
          dismiss(id)
        }, duration)
      }
    },
    [dismiss]
  )

  const success = useCallback((title: string, message?: string) => toast({ title, message, type: 'success' }), [toast])
  const error = useCallback((title: string, message?: string) => toast({ title, message, type: 'error' }), [toast])
  const warning = useCallback((title: string, message?: string) => toast({ title, message, type: 'warning' }), [toast])
  const info = useCallback((title: string, message?: string) => toast({ title, message, type: 'info' }), [toast])

  const icons: Record<ToastType, React.ReactNode> = {
    success: <CheckCircle2 className="w-4 h-4 text-emerald-500 shrink-0" aria-hidden="true" />,
    warning: <AlertTriangle className="w-4 h-4 text-amber-500 shrink-0" aria-hidden="true" />,
    error: <AlertCircle className="w-4 h-4 text-red-500 shrink-0" aria-hidden="true" />,
    info: <Info className="w-4 h-4 text-indigo-500 shrink-0" aria-hidden="true" />,
  }

  const borders: Record<ToastType, string> = {
    success: 'border-emerald-200 dark:border-emerald-900',
    warning: 'border-amber-200 dark:border-amber-900',
    error: 'border-red-200 dark:border-red-900',
    info: 'border-indigo-200 dark:border-indigo-900',
  }

  return (
    <ToastContext.Provider value={{ toast, success, error, warning, info, dismiss }}>
      {children}
      <div
        aria-live="polite"
        className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 max-w-sm w-full pointer-events-none"
      >
        {toasts.map((t) => (
          <div
            key={t.id}
            role="status"
            className={cn(
              'pointer-events-auto p-3 bg-card text-foreground rounded-lg border shadow-lg flex items-start gap-3 animate-in slide-in-from-bottom-5 duration-200',
              borders[t.type]
            )}
          >
            {icons[t.type]}
            <div className="flex-1 min-w-0 pr-1">
              <h4 className="text-xs font-semibold">{t.title}</h4>
              {t.message && (
                <p className="text-[11px] text-muted-foreground mt-0.5 leading-snug break-words">
                  {t.message}
                </p>
              )}
            </div>
            <button
              onClick={() => dismiss(t.id)}
              aria-label="Dismiss toast"
              className="text-muted-foreground hover:text-foreground p-0.5 rounded-sm transition-colors"
            >
              <X className="w-3.5 h-3.5" aria-hidden="true" />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

export function useToast(): ToastContextType {
  const context = useContext(ToastContext)
  if (!context) {
    throw new Error('useToast must be used within a ToastProvider')
  }
  return context
}
