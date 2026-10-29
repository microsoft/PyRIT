/**
 * Start a browser download of `blob` as `filename` by clicking a temporary link
 * to an object URL. The link is removed and the URL revoked even if the click
 * throws.
 */
export function downloadFile(blob: Blob, filename: string): void {
  const objectUrl = URL.createObjectURL(blob)
  try {
    const link = document.createElement('a')
    link.href = objectUrl
    link.download = filename
    document.body.appendChild(link)
    try {
      link.click()
    } finally {
      link.remove()
    }
  } finally {
    URL.revokeObjectURL(objectUrl)
  }
}
