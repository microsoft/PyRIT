import { mapMediaUrl, mediaAccessLink } from './media'

const SIGNED = 'https://account.blob.core.windows.net/container/seed-prompt-entries/clip.mp4?sig=signed'
const RAW_BLOB = 'https://account.blob.core.windows.net/container/seed-prompt-entries/clip.mp4'

describe('mapMediaUrl', () => {
  it('should prefer the backend display URL over the stored reference', () => {
    expect(mapMediaUrl({
      dataType: 'video_path',
      value: RAW_BLOB,
      valueUrl: SIGNED,
    })).toEqual({ status: 'preview', kind: 'video', url: SIGNED })
  })

  it('should preview a local file only through the media route', () => {
    expect(mapMediaUrl({
      dataType: 'image_path',
      value: '/dbdata/seed-prompt-entries/pic.png',
      valueUrl: '/api/media?path=%2Fdbdata%2Fseed-prompt-entries%2Fpic.png',
    })).toEqual({
      status: 'preview',
      kind: 'image',
      url: '/api/media?path=%2Fdbdata%2Fseed-prompt-entries%2Fpic.png',
    })
  })

  it('should treat a bare filesystem path as missing', () => {
    expect(mapMediaUrl({
      dataType: 'audio_path',
      value: 'C:\\dbdata\\seed-prompt-entries\\talk.wav',
    })).toEqual({ status: 'missing' })
    expect(mapMediaUrl({
      dataType: 'image_path',
      value: '/tmp/secret.png',
      valueUrl: '/tmp/secret.png',
    })).toEqual({ status: 'missing' })
  })

  it('should not turn text or active markup into a preview', () => {
    expect(mapMediaUrl({
      dataType: 'text',
      value: '<img src=x onerror=alert(1)> /tmp/secret.png',
    })).toEqual({ status: 'none' })
    expect(mapMediaUrl({
      dataType: 'image_path',
      value: '<img src=x onerror=alert(1)> /tmp/secret.png',
    })).toEqual({ status: 'missing' })
    expect(mapMediaUrl({
      dataType: 'url',
      value: 'https://example.test/picture.png',
    })).toEqual({ status: 'none' })
  })

  it('should refuse SVG and HTML instead of rendering them', () => {
    expect(mapMediaUrl({
      dataType: 'image_path',
      value: '/dbdata/seed-prompt-entries/mark.svg',
      valueUrl: '/api/media?path=%2Fdbdata%2Fseed-prompt-entries%2Fmark.svg',
    })).toEqual({
      status: 'unsupported',
      url: '/api/media?path=%2Fdbdata%2Fseed-prompt-entries%2Fmark.svg',
    })
    expect(mapMediaUrl({
      dataType: 'image_path',
      value: 'https://example.test/page.html',
      valueUrl: 'https://example.test/page.html',
    })).toEqual({ status: 'unsupported', url: null })
    expect(mapMediaUrl({
      dataType: 'image_path',
      value: 'data:image/svg+xml;base64,PHN2Zy8+',
      valueUrl: 'data:image/svg+xml;base64,PHN2Zy8+',
    })).toEqual({ status: 'unsupported', url: null })
  })

  it('should preview a safe data URI and leave other data URIs download-only', () => {
    const png = 'data:image/png;base64,AAAA'
    expect(mapMediaUrl({ dataType: 'image_path', value: png, valueUrl: png })).toEqual({
      status: 'preview',
      kind: 'image',
      url: png,
    })
    const pdf = 'data:application/pdf;base64,AAAA'
    expect(mapMediaUrl({ dataType: 'image_path', value: pdf, valueUrl: pdf })).toEqual({
      status: 'unsupported',
      url: null,
    })
  })

  it('should keep binary files downloadable when the media route can serve them', () => {
    const url = '/api/media?path=%2Fdbdata%2Fseed-prompt-entries%2Fnotes.bin'
    expect(mapMediaUrl({
      dataType: 'binary_path',
      value: '/dbdata/seed-prompt-entries/notes.bin',
      valueUrl: url,
    })).toEqual({ status: 'unsupported', url })
  })

  it('should not preview a data type that disagrees with the file extension', () => {
    expect(mapMediaUrl({
      dataType: 'image_path',
      value: RAW_BLOB,
      valueUrl: SIGNED,
    })).toEqual({ status: 'unsupported', url: SIGNED })
  })

  it('should preview audio from an encoded media-route path', () => {
    const url = '/api/media?path=' + encodeURIComponent('/dbdata/seed-prompt-entries/talk.wav')
    expect(mapMediaUrl({
      dataType: 'audio_path',
      value: '/dbdata/seed-prompt-entries/talk.wav',
      valueUrl: url,
    })).toEqual({ status: 'preview', kind: 'audio', url })
  })

  it('should return missing when a media value is empty', () => {
    expect(mapMediaUrl({ dataType: 'video_path', value: '   ', valueUrl: null })).toEqual({ status: 'missing' })
  })
})

describe('mediaAccessLink', () => {
  const origin = 'http://127.0.0.1:3000'

  it('should download same-origin, blob, and data urls in place', () => {
    expect(mediaAccessLink('/api/media?path=%2Fdbdata%2Fpic.png', 'pic.png', origin)).toEqual({
      href: '/api/media?path=%2Fdbdata%2Fpic.png',
      label: 'Download',
      downloadName: 'pic.png',
      openInNewTab: false,
    })
    expect(mediaAccessLink(`${origin}/api/media?path=%2Fdbdata%2Fpic.png`, 'pic.png', origin).label).toBe('Download')
    expect(mediaAccessLink('blob:http://127.0.0.1:3000/11111111-1111-1111-1111-111111111111', 'clip.mp4', origin).openInNewTab).toBe(false)
    expect(mediaAccessLink('data:image/png;base64,AAAA', 'pic.png', origin).openInNewTab).toBe(false)
  })

  it('should open a signed blob url in a new tab', () => {
    expect(mediaAccessLink(SIGNED, 'clip.mp4', origin)).toEqual({
      href: SIGNED,
      label: 'Open in new tab',
      downloadName: null,
      openInNewTab: true,
    })
  })
})
