import TechniqueEditorDialog from '@/components/Techniques/TechniqueEditorDialog'

interface CreateTechniqueDialogProps {
  onClose: () => void
  onCreated: () => void
}

/** Registry creation uses the shared form, but retains its global admission path. */
export default function CreateTechniqueDialog(props: CreateTechniqueDialogProps) {
  return <TechniqueEditorDialog {...props} />
}
