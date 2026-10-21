import TechniqueEditor from '@/components/Techniques/TechniqueEditor'

interface CreateTechniqueDialogProps {
  onClose: () => void
  onCreated: () => void
}

/** Registry creation uses the shared form, but retains its global admission path. */
export default function CreateTechniqueDialog(props: CreateTechniqueDialogProps) {
  return <TechniqueEditor {...props} />
}
