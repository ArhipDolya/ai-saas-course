import { useEffect, useState } from "react";

const emptySummary = {
  total_income: "0.00",
  total_expense: "0.00",
  balance: "0.00",
};

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

export default function AdminDashboard() {
  const [summary, setSummary] = useState(emptySummary);
  const [transactions, setTransactions] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    let isActive = true;

    async function loadDashboardData() {
      try {
        setIsLoading(true);
        setError("");

        const [transactionsResponse, summaryResponse] = await Promise.all([
          fetch("/api/transactions/", { signal: controller.signal }),
          fetch("/api/summary/", { signal: controller.signal }),
        ]);

        if (!transactionsResponse.ok || !summaryResponse.ok) {
          throw new Error("API request failed");
        }

        const [transactionsData, summaryData] = await Promise.all([
          transactionsResponse.json(),
          summaryResponse.json(),
        ]);

        if (!isActive) {
          return;
        }

        setTransactions(transactionsData);
        setSummary(summaryData);
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

  return (
    <main className="dashboard">
      <header className="dashboard__header">
        <div>
          <p className="dashboard__label">Finance Bot</p>
          <h1>AdminDashboard</h1>
        </div>
      </header>

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
        <div className="transactions__header">
          <span>Дата</span>
          <span>Сума</span>
          <span>Категорія</span>
        </div>

        {isLoading ? (
          <div className="transactions__empty">Завантаження...</div>
        ) : null}

        {!isLoading && error ? (
          <div className="transactions__empty" role="alert">
            {error}
          </div>
        ) : null}

        {!isLoading && !error && transactions.length === 0 ? (
          <div className="transactions__empty">Операцій поки немає</div>
        ) : null}

        {!isLoading && !error
          ? transactions.map((transaction) => (
              <div className="transactions__row" key={transaction.id}>
                <span>{formatDate(transaction.created_at)}</span>
                <span>{formatMoney(transaction.amount)}</span>
                <span>{transaction.category_name || "-"}</span>
              </div>
            ))
          : null}
      </section>
    </main>
  );
}
