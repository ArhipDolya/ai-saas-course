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

      setMessages((prev) => [...prev, { role: 'assistant', text: result.message, pendingAction: result.pending_action }])
    } catch {
      setError('Не вдалося зв\u2019язатися з сервером. Перевір підключення.')
    } finally {
      sendingRef.current = false
      setIsSending(false)
    }
  }, [inputValue, telegramId])

  const handleAction = useCallback(async (actionId, isConfirm, messageIndex) => {
    if (sendingRef.current) return
    sendingRef.current = true
    setIsSending(true)
    setError('')

    try {
      const endpoint = isConfirm ? 'confirm' : 'cancel'
      const query = new URLSearchParams({ telegram_id: telegramId })
      const response = await fetch(`/api/ai/actions/${actionId}/${endpoint}?${query}`, {
        method: 'POST',
      })

      if (!response.ok) {
        const result = await response.json().catch(() => null)
        setError(result?.detail || 'Не вдалося виконати дію.')
        return
      }

      setMessages(prev => prev.map((msg, idx) => {
        if (idx === messageIndex) {
          return { ...msg, pendingAction: null }
        }
        return msg
      }))

      const successMsg = { role: 'assistant', text: isConfirm ? '✅ Дію успішно виконано!' : '❌ Дію скасовано.' }
      setMessages(prev => [...prev, successMsg])

      if (isConfirm) {
        window.dispatchEvent(new CustomEvent('transaction-updated'))
      }

    } catch {
      setError('Помилка з\'єднання з сервером.')
    } finally {
      sendingRef.current = false
      setIsSending(false)
    }
  }, [telegramId])

  function handleKeyDown(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      handleSend()
    }
  }

  if (!telegramId || !isReady) return null

  const getActionTitle = (type) => {
    if (type === 'create_transaction') return 'Створення транзакції'
    if (type === 'update_transaction') return 'Оновлення транзакції'
    if (type === 'delete_transaction') return 'Видалення транзакції'
    return 'Невідома дія'
  }

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
                
                {msg.pendingAction && (
                  <div className="pending-action-card">
                    <h4>Підтвердження дії</h4>
                    <p><strong>Тип:</strong> {getActionTitle(msg.pendingAction.type)}</p>
                    {msg.pendingAction.payload.amount && (
                      <p><strong>Сума:</strong> {msg.pendingAction.payload.amount} грн</p>
                    )}
                    {msg.pendingAction.payload.category && (
                      <p><strong>Категорія:</strong> {msg.pendingAction.payload.category}</p>
                    )}
                    <div className="pending-action-buttons">
                      <button onClick={() => handleAction(msg.pendingAction.action_id, true, index)} className="confirm-btn">Підтвердити</button>
                      <button onClick={() => handleAction(msg.pendingAction.action_id, false, index)} className="cancel-btn">Скасувати</button>
                    </div>
                  </div>
                )}
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
