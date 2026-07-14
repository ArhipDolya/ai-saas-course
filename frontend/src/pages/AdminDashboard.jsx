import { useEffect, useState } from "react";

const MAX_AMOUNT = 999999999.99;

const emptySummary = {
  total_income: "0.00",
  total_expense: "0.00",
  balance: "0.00",
};

const emptyForm = {
  type: "expense",
  amount: "",
  category: "",
  description: "",
  date: "",
};

const transactionFilters = ["all", "income", "expense"];

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);

  if (!response.ok) {
    const error = new Error("API request failed");
    error.status = response.status;
    throw error;
  }

  return response.json();
}

async function fetchDashboardData(signal) {
  const [transactions, summary] = await Promise.all([
    fetchJson("/api/transactions/", { signal }),
    fetchJson("/api/summary/", { signal }),
  ]);

  return { transactions, summary };
}

function formatDate(value) {
  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return "-";
  }

  return new Intl.DateTimeFormat("uk-UA", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function formatMoney(value) {
  const amount = Number(value);

  if (Number.isNaN(amount)) {
    return "0.00";
  }

  return amount.toLocaleString("uk-UA", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function getLocalDateTimeInputValue(date = new Date()) {
  const timezoneOffset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - timezoneOffset).toISOString().slice(0, 16);
}

function validateTransactionForm(form) {
  const errors = {};
  const amount = form.amount.trim().replace(",", ".");
  const category = form.category.trim();
  const description = form.description.trim();

  if (!["income", "expense"].includes(form.type)) {
    errors.type = "Обери income або expense";
  }

  if (!amount) {
    errors.amount = "Вкажи суму";
  } else if (!/^\d+(\.\d{1,2})?$/.test(amount)) {
    errors.amount = "Сума має бути числом з максимум 2 знаками після крапки";
  } else {
    const amountNumber = Number(amount);
    if (amountNumber <= 0) {
      errors.amount = "Сума має бути більшою за 0";
    } else if (amountNumber > MAX_AMOUNT) {
      errors.amount = "Сума занадто велика";
    }
  }

  if (!category) {
    errors.category = "Вкажи категорію";
  } else if (category.length > 100) {
    errors.category = "Категорія має бути до 100 символів";
  } else if (![...category].some((char) => /\p{L}/u.test(char))) {
    errors.category = "Категорія має містити текст";
  }

  if (description.length > 500) {
    errors.description = "Опис має бути до 500 символів";
  }

  if (form.date) {
    const selectedDate = new Date(form.date);
    if (Number.isNaN(selectedDate.getTime())) {
      errors.date = "Некоректна дата";
    } else if (selectedDate.getTime() > Date.now()) {
      errors.date = "Дата не може бути в майбутньому";
    }
  }

  return errors;
}

function buildTransactionPayload(form) {
  const payload = {
    type: form.type,
    amount: form.amount.trim().replace(",", "."),
    category: form.category.trim(),
    description: form.description.trim() || null,
  };

  if (form.date) {
    payload.date = new Date(form.date).toISOString();
  }

  return payload;
}

export default function AdminDashboard() {
  const [activeView, setActiveView] = useState("overview");
  const [summary, setSummary] = useState(emptySummary);
  const [transactions, setTransactions] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");
  const [form, setForm] = useState(emptyForm);
  const [formErrors, setFormErrors] = useState({});
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const [submitMessage, setSubmitMessage] = useState("");
  const [pendingDeleteId, setPendingDeleteId] = useState(null);
  const [deletingTransactionId, setDeletingTransactionId] = useState(null);
  const [deleteError, setDeleteError] = useState("");
  const [transactionFilter, setTransactionFilter] = useState("all");
  const [adminPasswordInput, setAdminPasswordInput] = useState("");
  const [adminPassword, setAdminPassword] = useState("");
  const [isAdminAuthenticated, setIsAdminAuthenticated] = useState(false);
  const [isAuthenticating, setIsAuthenticating] = useState(false);
  const [authError, setAuthError] = useState("");

  const filteredTransactions =
    transactionFilter === "all"
      ? transactions
      : transactions.filter((transaction) => transaction.type === transactionFilter);

  async function refreshDashboardData(signal) {
    const dashboardData = await fetchDashboardData(signal);
    setTransactions(dashboardData.transactions);
    setSummary(dashboardData.summary);
  }

  useEffect(() => {
    const controller = new AbortController();
    let isActive = true;

    async function loadDashboardData() {
      try {
        setIsLoading(true);
        setError("");

        const dashboardData = await fetchDashboardData(controller.signal);

        if (!isActive) {
          return;
        }

        setTransactions(dashboardData.transactions);
        setSummary(dashboardData.summary);
      } catch (caughtError) {
        if (isActive && caughtError?.name !== "AbortError") {
          setError("Не вдалося завантажити дані");
        }
      } finally {
        if (isActive) {
          setIsLoading(false);
        }
      }
    }

    loadDashboardData();

    return () => {
      isActive = false;
      controller.abort();
    };
  }, []);

  async function handleAdminLogin(event) {
    event.preventDefault();

    if (!adminPasswordInput) {
      setAuthError("Введи пароль");
      return;
    }

    try {
      setIsAuthenticating(true);
      setAuthError("");

      await fetchJson("/api/admin/verify-password", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ password: adminPasswordInput }),
      });

      setAdminPassword(adminPasswordInput);
      setAdminPasswordInput("");
      setIsAdminAuthenticated(true);
    } catch (caughtError) {
      setAuthError(
        caughtError?.status === 401
          ? "Невірний пароль"
          : "Не вдалося перевірити пароль",
      );
    } finally {
      setIsAuthenticating(false);
    }
  }

  function updateFormField(event) {
    const { name, value } = event.target;
    setForm((currentForm) => ({ ...currentForm, [name]: value }));
    setFormErrors((currentErrors) => ({ ...currentErrors, [name]: "" }));
    setSubmitError("");
    setSubmitMessage("");
  }

  async function handleSubmit(event) {
    event.preventDefault();

    const validationErrors = validateTransactionForm(form);
    setFormErrors(validationErrors);
    setSubmitError("");
    setSubmitMessage("");

    if (Object.keys(validationErrors).length > 0) {
      return;
    }

    try {
      setIsSubmitting(true);

      await fetchJson("/api/transactions", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Admin-Auth": adminPassword,
        },
        body: JSON.stringify(buildTransactionPayload(form)),
      });

      await refreshDashboardData();
      setForm(emptyForm);
      setSubmitMessage("Операцію створено");
    } catch (caughtError) {
      if (caughtError?.status === 401) {
        setAdminPassword("");
        setIsAdminAuthenticated(false);
        setAuthError("Пароль адміністратора недійсний. Введи його ще раз.");
        return;
      }

      setSubmitError("Не вдалося створити операцію");
    } finally {
      setIsSubmitting(false);
    }
  }

  function requestDelete(transactionId) {
    setPendingDeleteId(transactionId);
    setDeleteError("");
  }

  function cancelDelete() {
    setPendingDeleteId(null);
    setDeleteError("");
  }

  function changeTransactionFilter(filter) {
    setTransactionFilter(filter);
    setPendingDeleteId(null);
    setDeleteError("");
  }

  async function confirmDelete(transactionId) {
    try {
      setDeletingTransactionId(transactionId);
      setDeleteError("");

      await fetchJson(`/api/transactions/${transactionId}`, {
        method: "DELETE",
      });

      await refreshDashboardData();
      setPendingDeleteId(null);
    } catch {
      setDeleteError("Не вдалося видалити операцію");
    } finally {
      setDeletingTransactionId(null);
    }
  }

  return (
    <main className="dashboard">
      <header className="dashboard__header">
        <div>
          <p className="dashboard__label">Finance Bot</p>
          <h1>AdminDashboard</h1>
        </div>

        <div className="dashboard__actions" aria-label="Перемикання сторінок">
          <button
            className={activeView === "overview" ? "view-button view-button--active" : "view-button"}
            type="button"
            onClick={() => setActiveView("overview")}
          >
            Огляд
          </button>
          <button
            className={activeView === "create" ? "view-button view-button--active" : "view-button"}
            type="button"
            onClick={() => setActiveView("create")}
          >
            Створити операцію
          </button>
        </div>
      </header>

      {activeView === "overview" ? (
        <>
          <section className="dashboard__grid" aria-label="Фінансовий огляд">
            <article className="metric metric--income">
              <span>Доходи</span>
              <strong>{formatMoney(summary.total_income)}</strong>
            </article>
            <article className="metric metric--expense">
              <span>Витрати</span>
              <strong>{formatMoney(summary.total_expense)}</strong>
            </article>
            <article className="metric metric--balance">
              <span>Баланс</span>
              <strong>{formatMoney(summary.balance)}</strong>
            </article>
          </section>

          <section className="transactions" aria-label="Операції">
            <div className="transactions__toolbar">
              <div className="transaction-filters" aria-label="Фільтр операцій">
                {transactionFilters.map((filter) => (
                  <button
                    aria-pressed={transactionFilter === filter}
                    className={
                      transactionFilter === filter
                        ? "filter-button filter-button--active"
                        : "filter-button"
                    }
                    key={filter}
                    type="button"
                    onClick={() => changeTransactionFilter(filter)}
                  >
                    {filter}
                  </button>
                ))}
              </div>
            </div>

            <div className="transactions__header">
              <span>Дата</span>
              <span>Сума</span>
              <span>Категорія</span>
              <span>Дії</span>
            </div>

            {isLoading ? (
              <div className="transactions__empty">Завантаження...</div>
            ) : null}

            {!isLoading && error ? (
              <div className="transactions__empty" role="alert">
                {error}
              </div>
            ) : null}

            {!isLoading && !error && deleteError ? (
              <div className="transactions__notice" role="alert">
                {deleteError}
              </div>
            ) : null}

            {!isLoading && !error && transactions.length === 0 ? (
              <div className="transactions__empty">Операцій поки немає</div>
            ) : null}

            {!isLoading && !error && transactions.length > 0 && filteredTransactions.length === 0 ? (
              <div className="transactions__empty">Операцій за цим фільтром немає</div>
            ) : null}

            {!isLoading && !error
              ? filteredTransactions.map((transaction) => (
                  <div className="transactions__row" key={transaction.id}>
                    <span>{formatDate(transaction.created_at)}</span>
                    <span>{formatMoney(transaction.amount)}</span>
                    <span>{transaction.category_name || "-"}</span>
                    <div className="transactions__actions">
                      {pendingDeleteId === transaction.id ? (
                        <>
                          <button
                            className="row-button row-button--danger"
                            disabled={deletingTransactionId === transaction.id}
                            type="button"
                            onClick={() => confirmDelete(transaction.id)}
                          >
                            {deletingTransactionId === transaction.id
                              ? "Видалення..."
                              : "Підтвердити"}
                          </button>
                          <button
                            className="row-button"
                            disabled={deletingTransactionId === transaction.id}
                            type="button"
                            onClick={cancelDelete}
                          >
                            Скасувати
                          </button>
                        </>
                      ) : (
                        <button
                          className="row-button row-button--ghost"
                          disabled={deletingTransactionId !== null}
                          type="button"
                          onClick={() => requestDelete(transaction.id)}
                        >
                          Видалити
                        </button>
                      )}
                    </div>
                  </div>
                ))
              : null}
          </section>
        </>
      ) : !isAdminAuthenticated ? (
        <section className="form-panel auth-panel" aria-label="Вхід адміністратора">
          <form className="auth-form" onSubmit={handleAdminLogin}>
            <div className="form-field">
              <label htmlFor="admin-password">Admin password</label>
              <input
                autoComplete="current-password"
                id="admin-password"
                name="admin-password"
                type="password"
                value={adminPasswordInput}
                onChange={(event) => {
                  setAdminPasswordInput(event.target.value);
                  setAuthError("");
                }}
              />
              {authError ? <span className="field-error">{authError}</span> : null}
            </div>

            <div className="form-actions">
              <button disabled={isAuthenticating} type="submit">
                {isAuthenticating ? "Перевірка..." : "Увійти"}
              </button>
            </div>
          </form>
        </section>
      ) : (
        <section className="form-panel" aria-label="Створення фінансової операції">
          <form className="transaction-form" onSubmit={handleSubmit}>
            <div className="form-field">
              <label htmlFor="transaction-type">Тип</label>
              <select
                id="transaction-type"
                name="type"
                value={form.type}
                onChange={updateFormField}
              >
                <option value="expense">expense</option>
                <option value="income">income</option>
              </select>
              {formErrors.type ? <span className="field-error">{formErrors.type}</span> : null}
            </div>

            <div className="form-field">
              <label htmlFor="transaction-amount">Сума</label>
              <input
                id="transaction-amount"
                name="amount"
                inputMode="decimal"
                placeholder="120.00"
                type="text"
                value={form.amount}
                onChange={updateFormField}
              />
              {formErrors.amount ? <span className="field-error">{formErrors.amount}</span> : null}
            </div>

            <div className="form-field">
              <label htmlFor="transaction-category">Категорія</label>
              <input
                id="transaction-category"
                name="category"
                placeholder="кава"
                type="text"
                value={form.category}
                onChange={updateFormField}
              />
              {formErrors.category ? <span className="field-error">{formErrors.category}</span> : null}
            </div>

            <div className="form-field">
              <label htmlFor="transaction-date">Дата</label>
              <input
                id="transaction-date"
                max={getLocalDateTimeInputValue()}
                name="date"
                type="datetime-local"
                value={form.date}
                onChange={updateFormField}
              />
              {formErrors.date ? <span className="field-error">{formErrors.date}</span> : null}
            </div>

            <div className="form-field form-field--wide">
              <label htmlFor="transaction-description">Опис</label>
              <textarea
                id="transaction-description"
                name="description"
                placeholder="Необов'язково"
                rows="4"
                value={form.description}
                onChange={updateFormField}
              />
              {formErrors.description ? (
                <span className="field-error">{formErrors.description}</span>
              ) : null}
            </div>

            <div className="form-actions">
              <button disabled={isSubmitting} type="submit">
                {isSubmitting ? "Збереження..." : "Зберегти операцію"}
              </button>
              {submitMessage ? <span className="form-message">{submitMessage}</span> : null}
              {submitError ? <span className="form-error">{submitError}</span> : null}
            </div>
          </form>
        </section>
      )}
    </main>
  );
}
