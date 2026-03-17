# Voice Full-Duplex LLM 通話系統 ToDoList

## ✅ Phase 1 — 基礎連線

* [ ] WebSocket Server
* [ ] Session ID 管理
* [ ] 多連線支援
* [ ] Binary Audio 傳輸
* [ ] JSON Control Message

---

## ✅ Phase 2 — Audio Input

* [ ] PCM chunk 切片
* [ ] Opus 支援 (未來APP)
* [ ] Audio Buffer
* [ ] Audio Queue
* [ ] 音量檢測

---

## ✅ Phase 3 — VAD / Silence Detection

* [ ] WebRTC VAD
* [ ] Silero VAD
* [ ] energy threshold
* [ ] silence timeout
* [ ] speech timeout
* [ ] end-of-utterance trigger

---

## ✅ Phase 4 — STT Stream

* [ ] Whisper streaming
* [ ] partial result
* [ ] final result
* [ ] text queue

---

## ✅ Phase 5 — LLM Stream

* [ ] token streaming
* [ ] delta parse
* [ ] history buffer
* [ ] context window control
* [ ] interrupt support

---

## ✅ Phase 6 — TTS Stream

* [ ] text chunk split
* [ ] phrase detect
* [ ] sentence detect
* [ ] TTS stream
* [ ] audio chunk queue

---

## ✅ Phase 7 — Audio Output

* [ ] audio queue
* [ ] websocket binary send
* [ ] client buffer
* [ ] smooth playback

---

## ✅ Phase 8 — Interrupt 機制

* [ ] user speak interrupt
* [ ] cancel tts
* [ ] cancel llm
* [ ] clear queue
* [ ] reset state

---

## ✅ Phase 9 — Session Manager

* [ ] session state
* [ ] audio state
* [ ] text state
* [ ] llm state
* [ ] tts state

---

## ✅ Phase 10 — API 化

* [ ] REST create session
* [ ] WS attach session
* [ ] API key auth
* [ ] rate limit
* [ ] usage log

---

## ✅ Phase 11 — APP 支援

* [ ] opus codec
* [ ] sample rate convert
* [ ] mobile latency optimize
* [ ] reconnect support

---

## ✅ Phase 12 — Production

* [ ] Redis session
* [ ] worker queue
* [ ] load balance
* [ ] metrics
* [ ] tracing
* [ ] logging
* [ ] error recovery

---
