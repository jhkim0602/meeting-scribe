// 마이크 입력을 16 kHz 16-bit PCM으로 바꿔 100 ms 단위로 메인 스레드에 보낸다.
class PcmCapture extends AudioWorkletProcessor {
  constructor() {
    super()
    this.ratio = sampleRate / 16000
    this.t = 0
    this.acc = 0
    this.accN = 0
    this.size = 1600
    this.buf = new Int16Array(this.size)
    this.n = 0
    this.enabled = true
    this.sq = 0
    this.sqN = 0
    this.peak = 0
    this.port.onmessage = (e) => {
      if (e.data && e.data.type === 'enable') this.enabled = !!e.data.value
    }
  }

  flush() {
    const rms = Math.sqrt(this.sq / Math.max(1, this.sqN))
    const msg = { type: 'chunk', rms, peak: this.peak, pcm: null }
    if (this.enabled) {
      const out = this.buf.slice(0, this.n)
      msg.pcm = out.buffer
      this.port.postMessage(msg, [out.buffer])
    } else {
      this.port.postMessage(msg)
    }
    this.n = 0
    this.sq = 0
    this.sqN = 0
    this.peak = 0
  }

  process(inputs) {
    const input = inputs[0]
    if (!input || input.length === 0) return true
    const ch = input[0]
    for (let i = 0; i < ch.length; i++) {
      const x = ch[i]
      this.acc += x
      this.accN++
      this.t += 1
      if (this.t >= this.ratio) {
        this.t -= this.ratio
        let y = this.acc / this.accN
        this.acc = 0
        this.accN = 0
        if (y > 1) y = 1
        else if (y < -1) y = -1
        this.sq += y * y
        this.sqN++
        const a = Math.abs(y)
        if (a > this.peak) this.peak = a
        this.buf[this.n++] = y < 0 ? y * 0x8000 : y * 0x7fff
        if (this.n >= this.size) this.flush()
      }
    }
    return true
  }
}

registerProcessor('pcm-capture', PcmCapture)
