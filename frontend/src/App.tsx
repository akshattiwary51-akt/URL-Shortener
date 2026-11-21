import { lazy, Suspense } from 'react'
import { Route, Routes } from 'react-router-dom'
import { AppShell } from './components/AppShell'
import { ProtectedRoute } from './components/ProtectedRoute'
import { PageLoading } from './components/ui'
import Landing from './pages/Landing'
import { LoginPage, RegisterPage } from './pages/AuthPages'
import NotFound from './pages/NotFound'

// Authenticated pages pull in the chart library; keep it out of the landing-page bundle.
const Dashboard = lazy(() => import('./pages/Dashboard'))
const Links = lazy(() => import('./pages/Links'))
const UrlDetail = lazy(() => import('./pages/UrlDetail'))
const Settings = lazy(() => import('./pages/Settings'))

export default function App() {
  return (
    <Suspense fallback={<PageLoading />}>
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route element={<ProtectedRoute />}>
          <Route element={<AppShell />}>
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/links" element={<Links />} />
            <Route path="/links/:code" element={<UrlDetail />} />
            <Route path="/settings" element={<Settings />} />
          </Route>
        </Route>
        <Route path="*" element={<NotFound />} />
      </Routes>
    </Suspense>
  )
}
