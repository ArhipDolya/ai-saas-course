import { useCallback, useEffect, useRef, useState } from 'react'
import './AiChat.css'

const MAX_MESSAGE_LENGTH = 200

function generateThreadId() {
  return `thread-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`
}

function readThreadId(telegramId) {
  return window.localStorage.getItem(`finance-chat-thread-${telegramId}`) ?? ''
}

function saveThreadId(telegramId, threadId) {
  window.localStorage.setItem(`finance-chat-thread-${telegramId}`, threadId)
}

function AiChat({ telegramId, isReady }) {
  const [isOpen, setIsOpen] = useState(false)
  const [messages, setMessages] = useState([])
  const [inputValue, setInputValue] = useState('')
  const [isSending, setIsSending] = useState(false)
  const [error, setError] = useState('')
  const sendingRef = useRef(false)
  const messagesEndRef = useRef(null)
  const threadIdRef = useRef('')

  // Ініціалізуємо або відновлюємо thread_id при зміні користувача
  useEffect(() => {
    if (!telegramId) {
      threadIdRef.current = ''
      setMessages([])
      return
    }

    const existingThreadId = readThreadId(telegramId)
    if (existingThreadId) {
      threadIdRef.current = existingThreadId
    } else {
      const newThreadId = generateThreadId()
      threadIdRef.current = newThreadId
      saveThreadId(telegramId, newThreadId)
    }

    setMessages([])
    setError('')
    setInputValue('')
  }, [telegramId])

  // Автоскрол до останнього повідомлення
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const handleSend = useCallback(async () => {
    const text = inputValue.trim()
    if (!text || sendingRef.current || !telegramId) return

    sendingRef.current = true
    setIsSending(true)
    setError('')

    const userMessage = { role: 'user', text }
    setMessages((prev) => [...prev, userMessage])
    setInputValue('')

    try {
      const query = new URLSearchParams({ telegram_id: telegramId })
      const response = await fetch(`/api/ai/chat?${query}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: text,
          thread_id: threadIdRef.current,
        }),
      })

      const result = await response.json().catch(() => null)

      if (!response.ok) {
        const detail = typeof result?.detail === 'string'
          ? result.detail
          : 'Не вдалося отримати відповідь від AI.'
        setError(detail)
        return
      }

      setMessages((prev) => [...prev, { role: 'assistant', text: result.message }])
    } catch {
      setError('Не вдалося зв\u2019язатися з сервером. Перевір підключення.')
    } finally {
      sendingRef.current = false
      setIsSending(false)
    }
  }, [inputValue, telegramId])

  function handleKeyDown(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      handleSend()
    }
  }

  if (!telegramId || !isReady) return null

  return (
    <>
      <button
        className="chat-toggle-button"
        type="button"
        onClick={() => setIsOpen((open) => !open)}
        aria-label={isOpen ? 'Закрити AI-чат' : 'Відкрити AI-чат'}
        aria-expanded={isOpen}
      >
        {isOpen ? '✕' : '💬'}
      </button>

      {isOpen && (
        <section className="chat-panel" aria-labelledby="chat-title">
          <header className="chat-header">
            <div>
              <p className="eyebrow">Gemini AI</p>
              <h2 id="chat-title">Фінансовий асистент</h2>
            </div>
            <button
              className="chat-close-button"
              type="button"
              onClick={() => setIsOpen(false)}
              aria-label="Закрити чат"
            >
              ✕
            </button>
          </header>

          <div className="chat-messages">
            {messages.length === 0 && (
              <p className="chat-empty">
                Запитай щось про свої фінанси. Наприклад: «На що я витрачаю найбільше?»
              </p>
            )}

            {messages.map((msg, index) => (
              <div
                key={`${msg.role}-${index}`}
                className={`chat-bubble chat-bubble-${msg.role}`}
              >
                <span className="chat-bubble-label">
                  {msg.role === 'user' ? 'Ти' : 'AI'}
                </span>
                <p>{msg.text}</p>
              </div>
            ))}

            {isSending && (
              <div className="chat-bubble chat-bubble-assistant">
                <span className="chat-bubble-label">AI</span>
                <p className="chat-typing">
                  <span className="typing-dot" />
                  <span className="typing-dot" />
                  <span className="typing-dot" />
                </p>
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>

          {error && (
            <p className="chat-error" role="alert">{error}</p>
          )}

          <form
            className="chat-input-form"
            onSubmit={(e) => { e.preventDefault(); handleSend() }}
          >
            <input
              className="chat-input"
              type="text"
              placeholder="Напиши повідомлення..."
              maxLength={MAX_MESSAGE_LENGTH}
              value={inputValue}
              disabled={isSending}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyDown={handleKeyDown}
              aria-label="Повідомлення для AI-асистента"
            />
            <button
              className="chat-send-button"
              type="submit"
              disabled={isSending || !inputValue.trim()}
              aria-label="Відправити повідомлення"
            >
              {isSending ? '⏳' : '➤'}
            </button>
          </form>
        </section>
      )}
    </>
  )
}

export default AiChat

