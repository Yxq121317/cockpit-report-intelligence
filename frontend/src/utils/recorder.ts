/**
 * 浏览器录音：采集麦克风 Float32 PCM，重采样到 16 kHz 单声道，
 * 编码为 PCM16 WAV 后交给后端语音转写接口。音频不落本地磁盘。
 */

type AudioContextConstructor = typeof AudioContext

interface WebkitWindow extends Window {
  webkitAudioContext?: AudioContextConstructor
}

const TARGET_SAMPLE_RATE = 16000
const BUFFER_SIZE = 4096

function mergeChunks(chunks: Float32Array[]): Float32Array {
  const length = chunks.reduce((total, chunk) => total + chunk.length, 0)
  const merged = new Float32Array(length)
  let offset = 0
  for (const chunk of chunks) {
    merged.set(chunk, offset)
    offset += chunk.length
  }
  return merged
}

function resample(samples: Float32Array, sourceRate: number): Float32Array {
  if (sourceRate === TARGET_SAMPLE_RATE || samples.length === 0) {
    return samples
  }
  const ratio = TARGET_SAMPLE_RATE / sourceRate
  const outputLength = Math.max(1, Math.round(samples.length * ratio))
  const output = new Float32Array(outputLength)
  const last = samples.length - 1
  for (let index = 0; index < outputLength; index += 1) {
    const position = index / ratio
    const left = Math.min(Math.floor(position), last)
    const right = Math.min(left + 1, last)
    const fraction = position - left
    output[index] =
      samples[left] * (1 - fraction) + samples[right] * fraction
  }
  return output
}

function encodeWav(samples: Float32Array): Blob {
  const dataLength = samples.length * 2
  const buffer = new ArrayBuffer(44 + dataLength)
  const view = new DataView(buffer)
  const writeText = (offset: number, text: string): void => {
    for (let index = 0; index < text.length; index += 1) {
      view.setUint8(offset + index, text.charCodeAt(index))
    }
  }

  writeText(0, 'RIFF')
  view.setUint32(4, 36 + dataLength, true)
  writeText(8, 'WAVE')
  writeText(12, 'fmt ')
  view.setUint32(16, 16, true)
  view.setUint16(20, 1, true)
  view.setUint16(22, 1, true)
  view.setUint32(24, TARGET_SAMPLE_RATE, true)
  view.setUint32(28, TARGET_SAMPLE_RATE * 2, true)
  view.setUint16(32, 2, true)
  view.setUint16(34, 16, true)
  writeText(36, 'data')
  view.setUint32(40, dataLength, true)

  let offset = 44
  for (const sample of samples) {
    const clamped = Math.max(-1, Math.min(1, sample))
    view.setInt16(
      offset,
      clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff,
      true,
    )
    offset += 2
  }
  return new Blob([buffer], { type: 'audio/wav' })
}

function micErrorMessage(reason: unknown): string {
  const name = reason instanceof DOMException ? reason.name : ''
  if (name === 'NotAllowedError' || name === 'SecurityError') {
    return (
      '麦克风权限被拒绝：请点击浏览器地址栏左侧的麦克风图标，把本站设为“允许”，' +
      '并确认 Windows 设置 → 隐私和安全性 → 麦克风已允许桌面应用访问，然后刷新页面重试。'
    )
  }
  if (name === 'NotFoundError' || name === 'DevicesNotFoundError') {
    return '未检测到麦克风，请连接麦克风设备后重试。'
  }
  if (name === 'NotReadableError' || name === 'TrackStartError') {
    return '麦克风被其他程序占用，请关闭占用麦克风的程序后重试。'
  }
  return reason instanceof Error && reason.message
    ? reason.message
    : '无法启动录音，请检查麦克风权限后重试'
}

export class WavRecorder {
  private stream: MediaStream | null = null
  private context: AudioContext | null = null
  private source: MediaStreamAudioSourceNode | null = null
  private processor: ScriptProcessorNode | null = null
  private mute: GainNode | null = null
  private chunks: Float32Array[] = []
  private startedAt = 0

  get durationSeconds(): number {
    if (!this.startedAt) return 0
    return (Date.now() - this.startedAt) / 1000
  }

  async start(): Promise<void> {
    if (this.context) {
      throw new Error('录音已经开始')
    }
    if (!window.isSecureContext) {
      throw new Error(
        '浏览器只允许在 localhost 或 HTTPS 下使用麦克风，请改用 http://localhost:5171 访问后重试',
      )
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error('当前浏览器不支持麦克风录音')
    }
    const AudioContextClass =
      window.AudioContext ||
      (window as WebkitWindow).webkitAudioContext
    if (!AudioContextClass) {
      throw new Error('当前浏览器不支持 Web Audio')
    }

    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
        },
      })
    } catch (reason) {
      throw new Error(micErrorMessage(reason))
    }
    this.context = new AudioContextClass()
    await this.context.resume()
    this.source = this.context.createMediaStreamSource(this.stream)
    this.processor = this.context.createScriptProcessor(
      BUFFER_SIZE,
      1,
      1,
    )
    this.mute = this.context.createGain()
    this.mute.gain.value = 0
    this.chunks = []
    this.processor.onaudioprocess = (event) => {
      this.chunks.push(
        new Float32Array(event.inputBuffer.getChannelData(0)),
      )
    }
    this.source.connect(this.processor)
    this.processor.connect(this.mute)
    this.mute.connect(this.context.destination)
    this.startedAt = Date.now()
  }

  async stop(): Promise<Blob> {
    const sampleRate = this.context?.sampleRate ?? TARGET_SAMPLE_RATE
    const chunks = this.chunks
    this.teardown()
    const merged = mergeChunks(chunks)
    if (merged.length === 0) {
      throw new Error('没有录到声音，请重试')
    }
    return encodeWav(resample(merged, sampleRate))
  }

  cancel(): void {
    this.teardown()
  }

  private teardown(): void {
    if (this.processor) {
      this.processor.onaudioprocess = null
      this.processor.disconnect()
    }
    this.source?.disconnect()
    this.mute?.disconnect()
    this.stream?.getTracks().forEach((track) => track.stop())
    if (this.context) {
      void this.context.close()
    }
    this.stream = null
    this.context = null
    this.source = null
    this.processor = null
    this.mute = null
    this.chunks = []
    this.startedAt = 0
  }
}
