import { useEffect, useMemo, useState } from 'react'
import './App.css'

const EMPTY_SUMMARY = {
  total_income: '0.00',
  total_expense: '0.00',
  balance: '0.00',
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
  hour: '2-digit',
  minute: '2-digit',
})

function formatMoney(value) {
  return currencyFormatter.format(Number(value) || 0)
}

function formatDate(value) {
  return dateFormatter.format(new Date(value))
}

function readSavedTelegramId() {
  return window.localStorage.getItem('finance-dashboard-telegram-id') ?? ''
}

function App() {
  const [inputTelegramId, setInputTelegramId] = useState(readSavedTelegramId)
  const [telegramId, setTelegramId] = useState(readSavedTelegramId)
  const [summary, setSummary] = useState(EMPTY_SUMMARY)
  const [transactions, setTransactions] = useState([])
  const [status, setStatus] = useState(telegramId ? 'loading' : 'idle')
  const [reloadVersion, setReloadVersion] = useState(0)

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

  const expensesByCategory = useMemo(() => {
    const totals = transactions.reduce((categories, transaction) => {
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
    const nextTelegramId = inputTelegramId.trim()

    if (!/^\d+$/.test(nextTelegramId)) {
      setStatus('invalid-id')
      return
    }

    window.localStorage.setItem('finance-dashboard-telegram-id', nextTelegramId)
    setTelegramId(nextTelegramId)
    setReloadVersion((currentVersion) => currentVersion + 1)
  }

  function handleRefresh() {
    if (telegramId) {
      setReloadVersion((currentVersion) => currentVersion + 1)
    }
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

          <form className="account-form" onSubmit={handleSubmit}>
            <label htmlFor="telegram-id">Telegram ID</label>
            <div className="account-controls">
              <input
                id="telegram-id"
                inputMode="numeric"
                pattern="[0-9]*"
                placeholder="Введи свій ID"
                value={inputTelegramId}
                onChange={(event) => setInputTelegramId(event.target.value)}
              />
              <button type="submit">Показати дані</button>
            </div>
          </form>
        </header>

        {status === 'invalid-id' && (
          <p className="notice notice-error" role="alert">
            Telegram ID має містити лише цифри.
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
            <span className="card-footnote">Доходи ще не додані до моделі</span>
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
                disabled={!telegramId || status === 'loading'}
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
              <span className="transaction-count">{transactions.length}</span>
            </div>

            <div className="table-wrapper">
              <table>
                <thead>
                  <tr>
                    <th scope="col">Дата</th>
                    <th scope="col">Тип</th>
                    <th scope="col">Сума</th>
                    <th scope="col">Категорія</th>
                  </tr>
                </thead>
                <tbody>
                  {transactions.length > 0 ? (
                    transactions.slice(0, 8).map((transaction) => (
                      <tr key={transaction.id}>
                        <td>{formatDate(transaction.created_at)}</td>
                        <td><span className="transaction-type expense-type">expense</span></td>
                        <td className="amount-cell">-{formatMoney(transaction.amount)}</td>
                        <td className="category-cell"><span className="category-dot" />{transaction.category}</td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td className="empty-table" colSpan="4">
                        {status === 'loading' ? 'Завантажуємо операції...' : 'Операцій поки немає.'}
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
