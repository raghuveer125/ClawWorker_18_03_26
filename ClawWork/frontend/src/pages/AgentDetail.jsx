import { useEffect } from 'react'
import { useParams } from 'react-router-dom'
import Dashboard from './Dashboard'

const AgentDetail = ({ onRouteAgentSelected }) => {
  const { signature } = useParams()

  useEffect(() => {
    if (signature && onRouteAgentSelected) {
      onRouteAgentSelected(decodeURIComponent(signature))
    }
  }, [signature, onRouteAgentSelected])

  return <Dashboard agents={[]} selectedAgent={signature} />
}

export default AgentDetail
