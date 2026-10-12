import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from '@/design/ThemeProvider'
import { ToastProvider } from '@/design/Toast'
import { AppShell } from '@/design/AppShell'
import { OverviewPage } from '@/pages/Overview'
import { LeadsPage } from '@/pages/Leads'
import { ApprovalPage } from '@/pages/Approval'
import { RepliesPage } from '@/pages/Replies'
import { CampaignsPage } from '@/pages/Campaigns'
import { SafetyPage } from '@/pages/Safety'
import { SettingsPage } from '@/pages/Settings'

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
              <Routes>
                <Route path="/" element={<OverviewPage />} />
                <Route path="/leads" element={<LeadsPage />} />
                <Route path="/approval" element={<ApprovalPage />} />
                <Route path="/replies" element={<RepliesPage />} />
                <Route path="/campaigns" element={<CampaignsPage />} />
                <Route path="/safety" element={<SafetyPage />} />
                <Route path="/settings" element={<SettingsPage />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
            </AppShell>
          </BrowserRouter>
        </ToastProvider>
      </ThemeProvider>
    </QueryClientProvider>
  )
}

export default App
