import { useState, useEffect } from 'react'
import { Lock, Eye, EyeOff, AlertCircle } from 'lucide-react'

// Password from environment variable
const APP_PASSWORD = import.meta.env.VITE_APP_PASSWORD || 'changeme'
const AUTH_KEY = 'clawwork_auth'
const AUTH_EXPIRY_DAYS = 7

const PasswordGate = ({ children }) => {
  const [isAuthenticated, setIsAuthenticated] = useState(false)
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [error, setError] = useState('')
  const [isLoading, setIsLoading] = useState(true)

  // Check if already authenticated
  useEffect(() => {
    const authData = localStorage.getItem(AUTH_KEY)
    if (authData) {
      try {
        const { expiry } = JSON.parse(authData)
        if (expiry && new Date(expiry) > new Date()) {
          setIsAuthenticated(true)
        } else {
          localStorage.removeItem(AUTH_KEY)
        }
      } catch {
        localStorage.removeItem(AUTH_KEY)
      }
    }
    setIsLoading(false)
  }, [])

  const handleSubmit = (e) => {
    e.preventDefault()
    setError('')

    if (password === APP_PASSWORD) {
      const expiry = new Date()
      expiry.setDate(expiry.getDate() + AUTH_EXPIRY_DAYS)
      localStorage.setItem(AUTH_KEY, JSON.stringify({ expiry: expiry.toISOString() }))
      setIsAuthenticated(true)
    } else {
      setError('Incorrect password')
      setPassword('')
    }
  }

  const handleLogout = () => {
    localStorage.removeItem(AUTH_KEY)
    setIsAuthenticated(false)
    setPassword('')
  }

  // Loading state
  if (isLoading) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
      </div>
    )
  }

  // Authenticated - render children
  if (isAuthenticated) {
    return (
      <>
        {children}
        {/* Logout button - fixed in corner */}
        <button
          onClick={handleLogout}
          className="fixed bottom-4 right-4 z-50 px-3 py-1.5 text-xs font-medium text-gray-500 bg-white border border-gray-200 rounded-lg shadow-sm hover:bg-gray-50 hover:text-gray-700 transition-colors"
          title="Logout"
        >
          Logout
        </button>
      </>
    )
  }

  // Password form
  return (
    <div className="min-h-screen bg-gradient-to-br from-gray-900 via-gray-800 to-gray-900 flex items-center justify-center p-4">
      <div className="w-full max-w-sm">
        <div className="bg-white rounded-2xl shadow-xl p-8">
          {/* Logo */}
          <div className="flex justify-center mb-6">
            <div className="w-16 h-16 bg-gradient-to-br from-primary-500 to-purple-600 rounded-2xl flex items-center justify-center shadow-lg">
              <Lock className="w-8 h-8 text-white" />
            </div>
          </div>

          {/* Title */}
          <h1 className="text-2xl font-bold text-gray-900 text-center mb-2">
            Trading Dashboard
          </h1>
          <p className="text-sm text-gray-500 text-center mb-6">
            Enter password to access
          </p>

          {/* Form */}
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="relative">
              <input
                type={showPassword ? 'text' : 'password'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Enter password"
                className="w-full px-4 py-3 pr-12 border border-gray-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent transition-all"
                autoFocus
                autoComplete="current-password"
              />
              <button
                type="button"
                onClick={() => setShowPassword(!showPassword)}
                className="absolute right-3 top-1/2 -translate-y-1/2 p-1 text-gray-400 hover:text-gray-600 transition-colors"
              >
                {showPassword ? (
                  <EyeOff className="w-5 h-5" />
                ) : (
                  <Eye className="w-5 h-5" />
                )}
              </button>
            </div>

            {/* Error message */}
            {error && (
              <div className="flex items-center gap-2 text-red-600 text-sm">
                <AlertCircle className="w-4 h-4" />
                <span>{error}</span>
              </div>
            )}

            <button
              type="submit"
              className="w-full py-3 px-4 bg-gradient-to-r from-primary-500 to-purple-600 text-white font-semibold rounded-xl hover:from-primary-600 hover:to-purple-700 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:ring-offset-2 transition-all shadow-lg"
            >
              Unlock
            </button>
          </form>

          {/* Footer */}
          <p className="text-xs text-gray-400 text-center mt-6">
            Access restricted to authorized users
          </p>
        </div>
      </div>
    </div>
  )
}

export default PasswordGate
