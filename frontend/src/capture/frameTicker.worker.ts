let timer: ReturnType<typeof setInterval> | undefined

self.onmessage = (e: MessageEvent<number>) => {
  clearInterval(timer)
  timer = setInterval(() => self.postMessage(0), e.data)
}
