import { Fragment, useEffect, useRef, useState } from "react";

import PendingActionCard from "../components/PendingActionCard.jsx";

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
    try {
      const payload = await response.json();
      error.detail = payload.detail;
    } catch {
      error.detail = "";
    }
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

function normalizeTextList(value) {
  return Array.isArray(value) ? value.filter(Boolean) : [];
}

function createChatMessage(role, content, extra = {}) {
  return {
    id: crypto.randomUUID(),
    role,
    content,
    ...extra,
  };
}

function normalizePendingAction(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }

  if (
    !Number.isInteger(value.action_id) ||
    value.action_id <= 0 ||
    typeof value.thread_id !== "string" ||
    typeof value.action_type !== "string" ||
    !value.payload ||
    typeof value.payload !== "object" ||
    Array.isArray(value.payload) ||
    value.status !== "pending"
  ) {
    return null;
  }

  return {
    action_id: value.action_id,
    thread_id: value.thread_id,
    action_type: value.action_type,
    payload: value.payload,
    status: value.status,
    error: "",
    result: null,
  };
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
  const [financialAnalysis, setFinancialAnalysis] = useState(null);
  const [isAnalyzingFinances, setIsAnalyzingFinances] = useState(false);
  const [analysisError, setAnalysisError] = useState("");
  const [chatMessages, setChatMessages] = useState([]);
  const [chatInput, setChatInput] = useState("");
  const [isChatLoading, setIsChatLoading] = useState(false);
  const [chatError, setChatError] = useState("");
  const [threadId, setThreadId] = useState(null);
  const chatRequestInFlightRef = useRef(false);
  const pendingActionRequestInFlightRef = useRef(false);
  const [pendingActionInProgressId, setPendingActionInProgressId] = useState(null);

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
      setFinancialAnalysis(null);
      setAnalysisError("");
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
      setFinancialAnalysis(null);
      setAnalysisError("");
      setPendingDeleteId(null);
    } catch {
      setDeleteError("Не вдалося видалити операцію");
    } finally {
      setDeletingTransactionId(null);
    }
  }

  async function analyzeFinances() {
    try {
      setIsAnalyzingFinances(true);
      setAnalysisError("");

      const analysis = await fetchJson("/api/ai/analyze-transactions", {
        method: "POST",
      });

      setFinancialAnalysis({
        summary: analysis.summary || "",
        top_expense_categories: normalizeTextList(analysis.top_expense_categories),
        risks: normalizeTextList(analysis.risks),
        advice: normalizeTextList(analysis.advice),
      });
    } catch (caughtError) {
      setFinancialAnalysis(null);
      setAnalysisError(
        caughtError?.detail || "Не вдалося виконати AI-аналіз фінансів",
      );
    } finally {
      setIsAnalyzingFinances(false);
    }
  }

  async function handleChatSubmit(event) {
    event.preventDefault();

    const message = chatInput.trim();
    if (!message) {
      setChatError("Введи повідомлення для AI-помічника");
      return;
    }

    if (chatRequestInFlightRef.current) {
      return;
    }

    chatRequestInFlightRef.current = true;
    setChatMessages((currentMessages) => [
      ...currentMessages,
      createChatMessage("user", message),
    ]);
    setChatInput("");
    setChatError("");
    setIsChatLoading(true);

    try {
      const response = await fetchJson("/api/ai/chat", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          message,
          thread_id: threadId,
        }),
      });

      if (typeof response.answer !== "string" || typeof response.thread_id !== "string") {
        throw new Error("Invalid AI chat response");
      }

      setThreadId(response.thread_id);
      setChatMessages((currentMessages) => [
        ...currentMessages,
        createChatMessage("assistant", response.answer, {
          pendingAction: normalizePendingAction(response.pending_action),
        }),
      ]);
    } catch {
      setChatError("Не вдалося отримати відповідь AI. Спробуйте ще раз.");
    } finally {
      chatRequestInFlightRef.current = false;
      setIsChatLoading(false);
    }
  }

  function updatePendingAction(chatMessageId, update) {
    setChatMessages((currentMessages) =>
      currentMessages.map((chatMessage) => {
        if (chatMessage.id !== chatMessageId || !chatMessage.pendingAction) {
          return chatMessage;
        }

        return {
          ...chatMessage,
          pendingAction: {
            ...chatMessage.pendingAction,
            ...update,
          },
        };
      }),
    );
  }

  async function handlePendingActionDecision(chatMessageId, action, decision) {
    if (
      action.status !== "pending" ||
      pendingActionRequestInFlightRef.current
    ) {
      return;
    }

    if (!adminPassword) {
      updatePendingAction(chatMessageId, {
        error: "Спершу увійдіть як адміністратор на вкладці створення операції.",
      });
      return;
    }

    pendingActionRequestInFlightRef.current = true;
    setPendingActionInProgressId(action.action_id);
    updatePendingAction(chatMessageId, { error: "" });

    const isConfirmation = decision === "confirm";
    const expectedStatus = isConfirmation ? "confirmed" : "canceled";
    const endpoint = isConfirmation ? "confirm" : "cancel";

    try {
      const response = await fetchJson(`/api/ai/actions/${action.action_id}/${endpoint}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Admin-Auth": adminPassword,
        },
        body: JSON.stringify({ thread_id: action.thread_id }),
      });

      if (response.action_id !== action.action_id || response.status !== expectedStatus) {
        throw new Error("Invalid pending action response");
      }

      updatePendingAction(chatMessageId, {
        status: response.status,
        result: response.result || null,
        error: "",
      });

      if (!isConfirmation) {
        return;
      }

      setFinancialAnalysis(null);
      setAnalysisError("");

      try {
        await refreshDashboardData();
        setError("");
      } catch {
        setError(
          "Дію підтверджено, але не вдалося оновити дані. Оновіть сторінку.",
        );
      }
    } catch (caughtError) {
      if (caughtError?.status === 401) {
        setAdminPassword("");
        setIsAdminAuthenticated(false);
        setAuthError("Пароль адміністратора недійсний. Введи його ще раз.");
      }

      updatePendingAction(chatMessageId, {
        error: isConfirmation
          ? "Не вдалося підтвердити дію. Спробуйте ще раз."
          : "Не вдалося відхилити дію. Спробуйте ще раз.",
      });
    } finally {
      pendingActionRequestInFlightRef.current = false;
      setPendingActionInProgressId(null);
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

          <section className="analysis-panel" aria-label="AI-аналіз фінансів">
            <div className="analysis-panel__header">
              <div>
                <h2>AI-аналіз фінансів</h2>
                <p>Короткий висновок по транзакціях з бази даних</p>
              </div>
              <button
                className="analysis-button"
                disabled={isAnalyzingFinances || isLoading}
                type="button"
                onClick={analyzeFinances}
              >
                {isAnalyzingFinances ? "Аналіз..." : "Аналіз Фінансів"}
              </button>
            </div>

            {analysisError ? (
              <div className="analysis-panel__error" role="alert">
                {analysisError}
              </div>
            ) : null}

            {financialAnalysis ? (
              <div className="analysis-cards">
                <article className="analysis-card analysis-card--wide">
                  <span>Висновок</span>
                  <p>{financialAnalysis.summary || "Немає висновку"}</p>
                </article>

                <article className="analysis-card">
                  <span>Топ категорії витрат</span>
                  {financialAnalysis.top_expense_categories.length > 0 ? (
                    <ul>
                      {financialAnalysis.top_expense_categories.map((category) => (
                        <li key={category}>{category}</li>
                      ))}
                    </ul>
                  ) : (
                    <p>Немає даних</p>
                  )}
                </article>

                <article className="analysis-card">
                  <span>Ризики</span>
                  {financialAnalysis.risks.length > 0 ? (
                    <ul>
                      {financialAnalysis.risks.map((risk) => (
                        <li key={risk}>{risk}</li>
                      ))}
                    </ul>
                  ) : (
                    <p>Ризиків не знайдено</p>
                  )}
                </article>

                <article className="analysis-card analysis-card--wide">
                  <span>Поради</span>
                  {financialAnalysis.advice.length > 0 ? (
                    <ul>
                      {financialAnalysis.advice.map((advice) => (
                        <li key={advice}>{advice}</li>
                      ))}
                    </ul>
                  ) : (
                    <p>Немає порад</p>
                  )}
                </article>
              </div>
            ) : null}
          </section>

          <section className="ai-chat" aria-labelledby="ai-chat-title">
            <div className="ai-chat__header">
              <div>
                <h2 id="ai-chat-title">AI-помічник</h2>
                <p>Запитайте про фінансові операції</p>
              </div>
            </div>

            <div className="ai-chat__messages" aria-live="polite" role="log">
              {chatMessages.length === 0 ? (
                <p className="ai-chat__empty">Почніть діалог з AI-помічником</p>
              ) : (
                chatMessages.map((chatMessage) => (
                  <Fragment key={chatMessage.id}>
                    <article
                      className={
                        chatMessage.role === "user"
                          ? "ai-chat__message ai-chat__message--user"
                          : "ai-chat__message ai-chat__message--assistant"
                      }
                    >
                      <span>{chatMessage.role === "user" ? "Ви" : "AI-помічник"}</span>
                      <p>{chatMessage.content}</p>
                    </article>

                    {chatMessage.pendingAction ? (
                      <PendingActionCard
                        action={chatMessage.pendingAction}
                        isDisabled={pendingActionInProgressId !== null}
                        isProcessing={
                          pendingActionInProgressId === chatMessage.pendingAction.action_id
                        }
                        requiresAuthentication={!isAdminAuthenticated}
                        onCancel={() =>
                          handlePendingActionDecision(
                            chatMessage.id,
                            chatMessage.pendingAction,
                            "cancel",
                          )
                        }
                        onConfirm={() =>
                          handlePendingActionDecision(
                            chatMessage.id,
                            chatMessage.pendingAction,
                            "confirm",
                          )
                        }
                      />
                    ) : null}
                  </Fragment>
                ))
              )}

              {isChatLoading ? (
                <article className="ai-chat__message ai-chat__message--assistant ai-chat__message--loading">
                  <span>AI-помічник</span>
                  <p>Формую відповідь...</p>
                </article>
              ) : null}
            </div>

            {chatError ? (
              <div className="ai-chat__error" role="alert">
                {chatError}
              </div>
            ) : null}

            <form className="ai-chat__form" onSubmit={handleChatSubmit}>
              <label className="visually-hidden" htmlFor="ai-chat-input">
                Повідомлення для AI-помічника
              </label>
              <textarea
                disabled={isChatLoading}
                id="ai-chat-input"
                maxLength={1000}
                placeholder="Напишіть повідомлення..."
                rows="3"
                value={chatInput}
                onChange={(event) => {
                  setChatInput(event.target.value);
                  setChatError("");
                }}
              />
              <button disabled={isChatLoading || !chatInput.trim()} type="submit">
                {isChatLoading ? "Надсилання..." : "Надіслати"}
              </button>
            </form>
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
