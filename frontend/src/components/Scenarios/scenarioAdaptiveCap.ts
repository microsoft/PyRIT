interface AdaptiveCapProvenance {
  selectedCandidateCount: number
  compatibleCandidateCount: number
  limit: number
  effectiveMaximum: number
}

function candidateContext({
  selectedCandidateCount,
  compatibleCandidateCount,
}: Pick<AdaptiveCapProvenance, 'selectedCandidateCount' | 'compatibleCandidateCount'>): string {
  const compatibleLabel = compatibleCandidateCount === 1 ? 'compatible candidate' : 'compatible candidates'
  const selectedLabel = selectedCandidateCount === 1 ? 'selected candidate' : 'selected candidates'
  return compatibleCandidateCount < selectedCandidateCount
    ? `${compatibleCandidateCount.toLocaleString()} ${compatibleLabel} from ${
      selectedCandidateCount.toLocaleString()
    } selected`
    : `${selectedCandidateCount.toLocaleString()} ${selectedLabel}`
}

export function formatAdaptiveCapMetadata(provenance: AdaptiveCapProvenance): string {
  return `${candidateContext(provenance)} · limit ${provenance.limit.toLocaleString()}`
}

export function formatAdaptiveCapFeedback(provenance: AdaptiveCapProvenance): string {
  return `${formatAdaptiveCapMetadata(provenance)} · effective maximum ${
    provenance.effectiveMaximum.toLocaleString()
  }.`
}

export function formatAdaptiveCapAccessibleRule(provenance: AdaptiveCapProvenance): string {
  return `the smaller of ${candidateContext(provenance)} and limit ${provenance.limit.toLocaleString()}`
}
