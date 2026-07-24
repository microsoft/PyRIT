import type { TargetInstance } from '../../types'
import TargetConfig from './TargetConfig'

interface ConfigPageProps {
  activeTarget: TargetInstance | null
  onSetActiveTarget: (target: TargetInstance) => void
}

export default function ConfigPage({ activeTarget, onSetActiveTarget }: ConfigPageProps) {
  return <TargetConfig activeTarget={activeTarget} onSetActiveTarget={onSetActiveTarget} />
}

