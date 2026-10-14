import { lazy, Suspense } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from '@/design/ThemeProvider'
import { ToastProvider } from '@/design/Toast'
import { AppShell } from '@/design/AppShell'

// Code-split pages for smaller bundle chunks and fast navigation
const OverviewPage = lazy(() => import('@/pages/Overview').then((m) => ({ default: m.OverviewPage })))
const LeadsPage = lazy(() => import('@/pages/Leads').then((m) => ({ default: m.LeadsPage })))
const ApprovalPage = lazy(() => import('@/pages/Approval').then((m) => ({ default: m.ApprovalPage })))
const RepliesPage = lazy(() => import('@/pages/Replies').then((m) => ({ default: m.RepliesPage })))
const CampaignsPage = lazy(() => import('@/pages/Campaigns').then((m) => ({ default: m.CampaignsPage })))
const MailboxesPage = lazy(() => import('@/pages/Mailboxes').then((m) => ({ default: m.MailboxesPage })))
const SafetyPage = lazy(() => import('@/pages/Safety').then((m) => ({ default: m.SafetyPage })))
const SettingsPage = lazy(() => import('@/pages/Settings').then((m) => ({ default: m.SettingsPage })))

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
})

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <ToastProvider>
          <BrowserRouter>
            <AppShell>
              <Suspense
                fallback={
                  <div className="flex h-64 items-center justify-center" role="status" aria-label="Loading page">
                    <div className="h-6 w-6 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                  </div>
                }
              >
                <Routes>
                  <Route path="/" element={<OverviewPage />} />
                  <Route path="/leads" element={<LeadsPage />} />
                  <Route path="/approval" element={<ApprovalPage />} />
                  <Route path="/replies" element={<RepliesPage />} />
                  <Route path="/campaigns" element={<CampaignsPage />} />
                  <Route path="/mailboxes" element={<MailboxesPage />} />
                  <Route path="/safety" element={<SafetyPage />} />
                  <Route path="/settings" element={<SettingsPage />} />
                  <Route path="*" element={<Navigate to="/" replace />} />
                </Routes>
              </Suspense>
            </AppShell>
          </BrowserRouter>
        </ToastProvider>
      </ThemeProvider>
    </QueryClientProvider>
  )
}

export default App
