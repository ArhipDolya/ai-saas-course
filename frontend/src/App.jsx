import { useEffect, useMemo, useRef, useState } from 'react'
import './App.css'

const EMPTY_SUMMARY = {
  total_income: '0.00',
  total_expense: '0.00',
  balance: '0.00',
}

const TRANSACTION_FILTERS = [
  { value: 'all', label: 'Усі' },
  { value: 'income', label: 'Доходи' },
  { value: 'expense', label: 'Витрати' },
]

const MAX_AMOUNT = 9_999_999_999.99
const MAX_CATEGORY_LENGTH = 100
const MAX_DESCRIPTION_LENGTH = 255
const MIN_TRANSACTION_DATE = '2000-01-01'
const EMPTY_TRANSACTION_FORM = {
  type: 'expense',
  amount: '',
  category: '',
  description: '',
  date: '',
}

const currencyFormatter = new Intl.NumberFormat('uk-UA', {
  style: 'currency',
  currency: 'UAH',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})

const dateFormatter = new Intl.DateTimeFormat('uk-UA', {
  day: '2-digit',
  month: 'short',
  year: 'numeric',
})

function formatMoney(value) {
  return currencyFormatter.format(Number(value) || 0)
}

function formatDate(value) {
  return dateFormatter.format(new Date(`${value}T12:00:00`))
}

function readSavedTelegramId() {
  return window.localStorage.getItem('finance-dashboard-telegram-id') ?? ''
}

function getLocalDateValue(date = new Date()) {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Europe/Kyiv', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(date)
  const part = (type) => parts.find((item) => item.type === type).value

  return `${part('year')}-${part('month')}-${part('day')}`
}

function validateTransactionForm(values) {
  const errors = {}
  const normalizedAmount = values.amount.trim().replace(',', '.')
  const amount = Number(normalizedAmount)
  const category = values.category.trim()
  const description = values.description.trim()
  const date = values.date
  const today = getLocalDateValue()

  if (!['income', 'expense'].includes(values.type)) {
    errors.type = 'Обери тип операції.'
  }

  if (!/^\d+(?:\.\d{1,2})?$/.test(normalizedAmount)) {
    errors.amount = 'Вкажи додатну суму з не більш ніж двома знаками після коми.'
  } else if (!Number.isFinite(amount) || amount <= 0) {
    errors.amount = 'Сума має бути більшою за 0.'
  } else if (amount > MAX_AMOUNT) {
    errors.amount = 'Сума не може перевищувати 9 999 999 999,99 грн.'
  }

  if (!category) {
    errors.category = 'Вкажи категорію.'
  } else if (category.length > MAX_CATEGORY_LENGTH) {
    errors.category = 'Категорія може містити до 100 символів.'
  }

  if (description.length > MAX_DESCRIPTION_LENGTH) {
    errors.description = 'Опис може містити до 255 символів.'
  }

  if (date) {
    const parsedDate = new Date(`${date}T00:00:00Z`)
    const isRealDate = /^\d{4}-\d{2}-\d{2}$/.test(date)
      && !Number.isNaN(parsedDate.getTime())
      && parsedDate.toISOString().slice(0, 10) === date

    if (!isRealDate) {
      errors.date = 'Вкажи коректну дату.'
    } else if (date < MIN_TRANSACTION_DATE || date > today) {
      errors.date = `Дата має бути від 01.01.2000 до ${formatDateForHint(today)}.`
    }
  }

  return errors
}

function formatDateForHint(value) {
  const [year, month, day] = value.split('-')
  return `${day}.${month}.${year}`
}

function App() {
  const [inputTelegramId, setInputTelegramId] = useState(readSavedTelegramId)
  const [telegramId, setTelegramId] = useState(readSavedTelegramId)
  const [summary, setSummary] = useState(EMPTY_SUMMARY)
  const [transactions, setTransactions] = useState([])
  const [transactionFilter, setTransactionFilter] = useState('all')
  const [status, setStatus] = useState(telegramId ? 'loading' : 'idle')
  const [reloadVersion, setReloadVersion] = useState(0)
  const [isTransactionFormOpen, setIsTransactionFormOpen] = useState(false)
  const [transactionForm, setTransactionForm] = useState(EMPTY_TRANSACTION_FORM)
  const [formErrors, setFormErrors] = useState({})
  const [formMessage, setFormMessage] = useState('')
  const [formError, setFormError] = useState('')
  const [isSaving, setIsSaving] = useState(false)
  const saveInProgress = useRef(false)
  const [deleteCandidateId, setDeleteCandidateId] = useState(null)
  const [deletingId, setDeletingId] = useState(null)
  const [deleteMessage, setDeleteMessage] = useState('')
  const [deleteError, setDeleteError] = useState('')
  const deleteInProgress = useRef(false)
  const isMutating = isSaving || deletingId !== null

  useEffect(() => {
    if (!telegramId) {
      return undefined
    }

    const controller = new AbortController()

    async function loadDashboard() {
      setStatus('loading')

      try {
        const query = new URLSearchParams({ telegram_id: telegramId })
        const [summaryResponse, transactionsResponse] = await Promise.all([
          fetch(`/api/summary?${query}`, { signal: controller.signal }),
          fetch(`/api/transactions?${query}`, { signal: controller.signal }),
        ])

        if (!summaryResponse.ok || !transactionsResponse.ok) {
          throw new Error('API request failed')
        }

        const [nextSummary, nextTransactions] = await Promise.all([
          summaryResponse.json(),
          transactionsResponse.json(),
        ])

        setSummary(nextSummary)
        setTransactions(nextTransactions)
        setStatus('ready')
      } catch (error) {
        if (error.name !== 'AbortError') {
          setStatus('error')
        }
      }
    }

    loadDashboard()

    return () => controller.abort()
  }, [telegramId, reloadVersion])

  const filteredTransactions = useMemo(
    () => transactionFilter === 'all'
      ? transactions
      : transactions.filter((transaction) => transaction.type === transactionFilter),
    [transactions, transactionFilter],
  )

  const expensesByCategory = useMemo(() => {
    const totals = transactions
      .filter((transaction) => transaction.type === 'expense')
      .reduce((categories, transaction) => {
        const currentTotal = categories.get(transaction.category) ?? 0
        categories.set(
          transaction.category,
          currentTotal + Number(transaction.amount),
        )
        return categories
      }, new Map())

    return [...totals.entries()]
      .map(([name, amount]) => ({ name, amount }))
      .sort((left, right) => right.amount - left.amount)
  }, [transactions])

  const maxCategoryExpense = Math.max(
    ...expensesByCategory.map((category) => category.amount),
    1,
  )

  function handleSubmit(event) {
    event.preventDefault()
    if (saveInProgress.current || deleteInProgress.current) return
    const nextTelegramId = inputTelegramId.trim()

    if (!/^\d+$/.test(nextTelegramId) || BigInt(nextTelegramId) <= 0n || BigInt(nextTelegramId) > 9223372036854775807n) {
      setStatus('invalid-id')
      return
    }

    window.localStorage.setItem('finance-dashboard-telegram-id', nextTelegramId)
    setSummary(EMPTY_SUMMARY)
    setTransactions([])
    setFormMessage('')
    setFormError('')
    setDeleteCandidateId(null)
    setDeleteMessage('')
    setDeleteError('')
    setTelegramId(nextTelegramId)
    setReloadVersion((currentVersion) => currentVersion + 1)
  }

  function handleRefresh() {
    if (saveInProgress.current || deleteInProgress.current) return
    if (telegramId) {
      setReloadVersion((currentVersion) => currentVersion + 1)
    }
  }

  function handleTransactionFormChange(event) {
    const { name, value } = event.target

    setTransactionForm((currentForm) => ({
      ...currentForm,
      [name]: value,
    }))
    setFormErrors((currentErrors) => ({
      ...currentErrors,
      [name]: undefined,
    }))
    setFormMessage('')
  }

  function handleTransactionFieldBlur(event) {
    const { name } = event.target
    const nextErrors = validateTransactionForm(transactionForm)

    setFormErrors((currentErrors) => ({
      ...currentErrors,
      [name]: nextErrors[name],
    }))
  }

  async function handleTransactionSave(event) {
    event.preventDefault()
    if (saveInProgress.current || deleteInProgress.current) return

    setFormError('')
    setFormMessage('')
    if (!telegramId) {
      setFormError('Введи Telegram ID і натисни «Показати дані», щоб обрати користувача.')
      return
    }

    const nextErrors = validateTransactionForm(transactionForm)
    if (Object.keys(nextErrors).length > 0) {
      setFormErrors(nextErrors)
      setFormMessage('')
      return
    }

    saveInProgress.current = true
    setIsSaving(true)
    setFormErrors({})
    try {
      const query = new URLSearchParams({ telegram_id: telegramId })
      const response = await fetch(`/api/transactions?${query}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          type: transactionForm.type,
          amount: transactionForm.amount.trim().replace(',', '.'),
          category: transactionForm.category.trim(),
          description: transactionForm.description.trim() || null,
          date: transactionForm.date || null,
        }),
      })
      const result = await response.json()

      if (!response.ok) {
        if (response.status === 422 && Array.isArray(result.detail)) {
          const messages = {
            type: 'Обери income або expense.',
            amount: 'Вкажи суму від 0,01 до 9 999 999 999,99 грн, до двох знаків після коми.',
            category: 'Вкажи категорію від 1 до 100 символів без недопустимих символів.',
            description: 'Опис має містити до 255 символів без недопустимих символів.',
            date: 'Вкажи коректну дату від 01.01.2000 до сьогодні (Europe/Kyiv).',
          }
          const serverErrors = {}
          for (const issue of result.detail) {
            const field = issue.loc?.[1]
            if (Object.hasOwn(messages, field)) serverErrors[field] = messages[field]
          }
          setFormErrors(serverErrors)
          setFormError('Перевір поля форми та обраний Telegram ID.')
        } else {
          setFormError(typeof result.detail === 'string'
            ? result.detail
            : 'Не вдалося зберегти операцію. Спробуй ще раз пізніше.')
        }
        return
      }

      setTransactionForm(EMPTY_TRANSACTION_FORM)
      setFormMessage(`Операцію №${result.id} збережено.`)
      setReloadVersion((currentVersion) => currentVersion + 1)
    } catch {
      setFormError('Не отримано підтвердження збереження. Онови список операцій перед повторною спробою, щоб уникнути дублювання.')
    } finally {
      saveInProgress.current = false
      setIsSaving(false)
    }
  }

  function handleTransactionFilterChange(type) {
    if (saveInProgress.current || deleteInProgress.current) return
    setTransactionFilter(type)
    setDeleteCandidateId(null)
    setDeleteMessage('')
    setDeleteError('')
  }

  function requestTransactionDelete(id) {
    if (saveInProgress.current || deleteInProgress.current) return
    setDeleteCandidateId(id)
    setDeleteError('')
    setDeleteMessage('')
  }

  async function handleTransactionDelete(id) {
    if (saveInProgress.current || deleteInProgress.current || !telegramId || deleteCandidateId !== id) return

    deleteInProgress.current = true
    setDeletingId(id)
    setDeleteError('')
    setDeleteMessage('')

    try {
      const query = new URLSearchParams({ telegram_id: telegramId })
      const response = await fetch(`/api/transactions/${encodeURIComponent(id)}?${query}`, {
        method: 'DELETE',
      })

      if (response.status === 204) {
        setTransactions((currentTransactions) => currentTransactions.filter((row) => row.id !== id))
        setDeleteCandidateId(null)
        setDeleteMessage('Операцію видалено.')
        setReloadVersion((currentVersion) => currentVersion + 1)
      } else if (response.status === 404) {
        setDeleteCandidateId(null)
        setDeleteError('Операцію не знайдено. Оновлюємо список.')
        setReloadVersion((currentVersion) => currentVersion + 1)
      } else {
        const result = await response.json().catch(() => null)
        setDeleteError(typeof result?.detail === 'string'
          ? result.detail
          : 'Не вдалося видалити операцію. Спробуй ще раз пізніше.')
      }
    } catch {
      setDeleteError('Не отримано підтвердження видалення. Перевір підключення та онови список операцій.')
    } finally {
      deleteInProgress.current = false
      setDeletingId(null)
    }
  }

  function handleTransactionFormClose() {
    if (saveInProgress.current) return
    setIsTransactionFormOpen(false)
    setTransactionForm(EMPTY_TRANSACTION_FORM)
    setFormErrors({})
    setFormMessage('')
    setFormError('')
  }

  const balance = Number(summary.balance)

  return (
    <main className="dashboard-shell">
      <section className="dashboard" aria-labelledby="dashboard-title">
        <header className="dashboard-header">
          <div>
            <p className="eyebrow">Finance SaaS</p>
            <h1 id="dashboard-title">Фінансовий огляд</h1>
            <p className="subtitle">Витрати, доходи та поточний баланс</p>
          </div>

          <div className="dashboard-actions">
            <button
              className="add-transaction-button"
              type="button"
              disabled={isMutating}
              onClick={() => {
                setIsTransactionFormOpen((isOpen) => !isOpen)
                setFormMessage('')
              }}
              aria-expanded={isTransactionFormOpen}
              aria-controls="transaction-form-panel"
            >
              {isTransactionFormOpen ? 'Закрити форму' : 'Додати операцію'}
            </button>

            <form className="account-form" onSubmit={handleSubmit}>
              <label htmlFor="telegram-id">Telegram ID</label>
              <div className="account-controls">
                <input
                  id="telegram-id"
                  inputMode="numeric"
                  pattern="[0-9]*"
                  placeholder="Введи свій ID"
                  value={inputTelegramId}
                  disabled={isMutating}
                  onChange={(event) => setInputTelegramId(event.target.value)}
                />
                <button type="submit" disabled={isMutating}>Показати дані</button>
              </div>
            </form>
          </div>
        </header>

        {isTransactionFormOpen && (
          <section
            className="transaction-form-panel"
            id="transaction-form-panel"
            aria-labelledby="transaction-form-title"
          >
            <div className="form-panel-heading">
              <div>
                <p className="eyebrow">Нова операція</p>
                <h2 id="transaction-form-title">Додати запис</h2>
              </div>
              <p className="form-panel-note">
                {telegramId
                  ? `Операцію буде збережено для Telegram ID ${telegramId}.`
                  : 'Спочатку введи Telegram ID та натисни «Показати дані».'}
              </p>
            </div>

            <form className="transaction-form" noValidate onSubmit={handleTransactionSave} aria-busy={isSaving}>
              <fieldset className="form-grid" disabled={isMutating} aria-label="Дані операції">
                <label className="form-field" htmlFor="transaction-type">
                  <span>Тип</span>
                  <select
                    id="transaction-type"
                    name="type"
                    value={transactionForm.type}
                    onChange={handleTransactionFormChange}
                    onBlur={handleTransactionFieldBlur}
                    aria-invalid={Boolean(formErrors.type)}
                    aria-describedby={formErrors.type ? 'transaction-type-error' : undefined}
                  >
                    <option value="expense">expense - Витрата</option>
                    <option value="income">income - Дохід</option>
                  </select>
                  {formErrors.type && <small id="transaction-type-error" className="field-error">{formErrors.type}</small>}
                </label>

                <label className="form-field" htmlFor="transaction-amount">
                  <span>Сума</span>
                  <input
                    id="transaction-amount"
                    name="amount"
                    inputMode="decimal"
                    placeholder="Наприклад, 100"
                    value={transactionForm.amount}
                    onChange={handleTransactionFormChange}
                    onBlur={handleTransactionFieldBlur}
                    aria-invalid={Boolean(formErrors.amount)}
                    aria-describedby={formErrors.amount ? 'transaction-amount-error' : undefined}
                  />
                  {formErrors.amount && <small id="transaction-amount-error" className="field-error">{formErrors.amount}</small>}
                </label>

                <label className="form-field" htmlFor="transaction-category">
                  <span>Категорія</span>
                  <input
                    id="transaction-category"
                    name="category"
                    maxLength={MAX_CATEGORY_LENGTH}
                    placeholder="Наприклад, хліб"
                    value={transactionForm.category}
                    onChange={handleTransactionFormChange}
                    onBlur={handleTransactionFieldBlur}
                    aria-invalid={Boolean(formErrors.category)}
                    aria-describedby={formErrors.category ? 'transaction-category-error' : undefined}
                  />
                  {formErrors.category && <small id="transaction-category-error" className="field-error">{formErrors.category}</small>}
                </label>

                <label className="form-field" htmlFor="transaction-date">
                  <span>Дата <em>необов'язково</em></span>
                  <input
                    id="transaction-date"
                    name="date"
                    type="date"
                    min={MIN_TRANSACTION_DATE}
                    max={getLocalDateValue()}
                    value={transactionForm.date}
                    onChange={handleTransactionFormChange}
                    onBlur={handleTransactionFieldBlur}
                    aria-invalid={Boolean(formErrors.date)}
                    aria-describedby={formErrors.date ? 'transaction-date-error' : 'transaction-date-hint'}
                  />
                  <small id="transaction-date-hint" className="field-hint">
                    Якщо не вказати дату, буде використано поточний день за київським часом.
                  </small>
                  {formErrors.date && <small id="transaction-date-error" className="field-error">{formErrors.date}</small>}
                </label>

                <label className="form-field form-field-wide" htmlFor="transaction-description">
                  <span>Опис <em>необов'язково</em></span>
                  <input
                    id="transaction-description"
                    name="description"
                    maxLength={MAX_DESCRIPTION_LENGTH}
                    placeholder="Наприклад, хліб для сніданку"
                    value={transactionForm.description}
                    onChange={handleTransactionFormChange}
                    onBlur={handleTransactionFieldBlur}
                    aria-invalid={Boolean(formErrors.description)}
                    aria-describedby={formErrors.description ? 'transaction-description-error' : undefined}
                  />
                  {formErrors.description && <small id="transaction-description-error" className="field-error">{formErrors.description}</small>}
                </label>
              </fieldset>

              {formMessage && <p className="form-success" role="status">{formMessage}</p>}
              {formError && <p className="notice notice-error" role="alert">{formError}</p>}

              <div className="form-actions">
                <button type="submit" disabled={isMutating || !telegramId}>
                  {isSaving ? 'Зберігаємо...' : 'Зберегти'}
                </button>
                <button className="secondary-button" type="button" onClick={handleTransactionFormClose} disabled={isSaving}>
                  Скасувати
                </button>
              </div>
            </form>
          </section>
        )}

        {status === 'invalid-id' && (
          <p className="notice notice-error" role="alert">
            Telegram ID має бути додатним цілим числом у допустимому діапазоні.
          </p>
        )}

        {status === 'error' && (
          <p className="notice notice-error" role="alert">
            Не вдалося завантажити дані. Перевір Telegram ID та запуск API.
          </p>
        )}

        {status === 'idle' && (
          <p className="notice">
            Введи Telegram ID, щоб побачити свої фінансові дані.
          </p>
        )}

        <section className="summary-grid" aria-label="Фінансові підсумки">
          <article className="summary-card income-card">
            <span className="card-label">Усього доходів</span>
            <strong>{formatMoney(summary.total_income)}</strong>
            <span className="card-footnote">За всіма записаними надходженнями</span>
          </article>

          <article className="summary-card expense-card">
            <span className="card-label">Усього витрат</span>
            <strong>{formatMoney(summary.total_expense)}</strong>
            <span className="card-footnote">За всіма записаними операціями</span>
          </article>

          <article className="summary-card balance-card">
            <span className="card-label">Поточний баланс</span>
            <strong className={balance < 0 ? 'negative-balance' : 'positive-balance'}>
              {formatMoney(summary.balance)}
            </strong>
            <span className="card-footnote">Доходи мінус витрати</span>
          </article>
        </section>

        <section className="content-grid">
          <article className="panel chart-panel">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">Структура витрат</p>
                <h2>Витрати за категоріями</h2>
              </div>
              <button
                className="refresh-button"
                type="button"
                onClick={handleRefresh}
                disabled={!telegramId || status === 'loading' || isMutating}
              >
                {status === 'loading' ? 'Оновлення...' : 'Оновити'}
              </button>
            </div>

            {expensesByCategory.length > 0 ? (
              <div className="chart" role="img" aria-label="Стовпчикова діаграма витрат за категоріями">
                {expensesByCategory.map((category, index) => (
                  <div className="chart-column" key={category.name}>
                    <span className="chart-value">{formatMoney(category.amount)}</span>
                    <div className="bar-track">
                      <div
                        className={`bar bar-${index % 4}`}
                        style={{ height: `${(category.amount / maxCategoryExpense) * 100}%` }}
                      />
                    </div>
                    <span className="chart-label" title={category.name}>{category.name}</span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="empty-chart">
                <span aria-hidden="true">▥</span>
                <p>Тут з'явиться розподіл витрат за категоріями.</p>
              </div>
            )}
          </article>

          <article className="panel transactions-panel">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">Останні записи</p>
                <h2>Операції</h2>
              </div>
              <span className="transaction-count" aria-label={`Знайдено операцій: ${filteredTransactions.length}`}>
                {filteredTransactions.length}
              </span>
            </div>

            <div className="transaction-filters" role="group" aria-label="Фільтр типу операцій">
              {TRANSACTION_FILTERS.map((filter) => (
                <button
                  key={filter.value}
                  type="button"
                  className="transaction-filter-button"
                  aria-pressed={transactionFilter === filter.value}
                  disabled={isMutating}
                  onClick={() => handleTransactionFilterChange(filter.value)}
                >
                  {filter.label}
                </button>
              ))}
            </div>

            {deleteMessage && <p className="transaction-notice form-success" role="status">{deleteMessage}</p>}
            {deleteError && <p className="transaction-notice notice notice-error" role="alert">{deleteError}</p>}

            <div className="table-wrapper">
              <table>
                <thead>
                  <tr>
                    <th scope="col">Дата</th>
                    <th scope="col">Тип</th>
                    <th scope="col">Сума</th>
                    <th scope="col">Категорія</th>
                    <th scope="col">Дії</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredTransactions.length > 0 ? (
                    filteredTransactions.slice(0, 8).map((transaction) => (
                      <tr key={transaction.id} aria-busy={deletingId === transaction.id}>
                        <td>{formatDate(transaction.date)}</td>
                        <td>
                          <span className={`transaction-type ${transaction.type}-type`}>
                            {transaction.type}
                          </span>
                        </td>
                        <td className={`amount-cell ${transaction.type}-amount`}>
                          {transaction.type === 'income' ? '+' : '-'}
                          {formatMoney(transaction.amount)}
                        </td>
                        <td className="category-cell">
                          <span className="category-dot" />
                          <span className="category-details">
                            <span>{transaction.category}</span>
                            {transaction.description && (
                              <small>{transaction.description}</small>
                            )}
                          </span>
                        </td>
                        <td className="transaction-actions-cell">
                          {deleteCandidateId === transaction.id ? (
                            <div className="delete-confirmation" role="group" aria-label={`Видалення операції №${transaction.id}`}>
                              <span>Видалити цю операцію?</span>
                              <button
                                className="delete-button confirm-delete-button"
                                type="button"
                                disabled={isMutating || status === 'loading'}
                                onClick={() => handleTransactionDelete(transaction.id)}
                              >
                                {deletingId === transaction.id ? 'Видаляємо...' : 'Підтвердити'}
                              </button>
                              <button
                                className="secondary-button cancel-delete-button"
                                type="button"
                                disabled={isMutating}
                                onClick={() => setDeleteCandidateId(null)}
                              >
                                Скасувати
                              </button>
                            </div>
                          ) : (
                            <button
                              className="delete-button"
                              type="button"
                              disabled={isMutating || status === 'loading' || !telegramId}
                              onClick={() => requestTransactionDelete(transaction.id)}
                              aria-label={`Видалити операцію №${transaction.id}: ${transaction.category}, ${formatMoney(transaction.amount)}`}
                            >
                              Видалити
                            </button>
                          )}
                        </td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td className="empty-table" colSpan="5">
                        {status === 'loading'
                          ? 'Завантажуємо операції...'
                          : transactionFilter === 'income'
                            ? 'Доходів поки немає.'
                            : transactionFilter === 'expense'
                              ? 'Витрат поки немає.'
                              : 'Операцій поки немає.'}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </article>
        </section>
      </section>
    </main>
  )
}

export default App
