// The Live page's frame timer (startTicker in liveCapture.ts). It only keeps time: a worker's
// timers keep running while the page is in the background, where the page's own are throttled.
// The page sends the interval (ms) once; the worker then posts a message every interval.

let timer: ReturnType<typeof setInterval> | undefined

self.onmessage = (e: MessageEvent<number>) => {
  clearInterval(timer)
  timer = setInterval(() => self.postMessage(0), e.data)
}
