const actionLabels = {
  create_transaction: "Створення операції",
  create_finance_transaction: "Створення операції",
  update_transaction_category: "Зміна категорії",
  update_transaction_sum: "Зміна суми",
  delete_transaction: "Видалення операції",
};

const statusLabels = {
  pending: "Очікує рішення",
  confirmed: "Підтверджено",
  canceled: "Відхилено",
};

function formatMoney(value) {
  const amount = Number(value);

  if (Number.isNaN(amount)) {
    return String(value || "-");
  }

  return amount.toLocaleString("uk-UA", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function formatDate(value) {
  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return String(value);
  }

  return new Intl.DateTimeFormat("uk-UA", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function getActionDetails(action) {
  const payload = action.payload || {};
  const details = [];
  const isAmountUpdate = action.action_type === "update_transaction_sum";
  const isCategoryUpdate = action.action_type === "update_transaction_category";
  const isCreate =
    action.action_type === "create_transaction" ||
    action.action_type === "create_finance_transaction";

  if (payload.transaction_id) {
    details.push(["Операція", `#${payload.transaction_id}`]);
  }

  if (payload.type && isCreate) {
    details.push(["Тип", payload.type === "income" ? "Дохід" : "Витрата"]);
  }

  if (payload.amount) {
    details.push([isAmountUpdate ? "Нова сума" : "Сума", formatMoney(payload.amount)]);
  }

  if (payload.category) {
    details.push([isCategoryUpdate ? "Нова категорія" : "Категорія", payload.category]);
  }

  if (payload.description) {
    details.push(["Опис", payload.description]);
  }

  if (payload.date) {
    details.push(["Дата", formatDate(payload.date)]);
  }

  return details;
}

function confirmedMessage(action) {
  const transactionId = action.result?.transaction?.id || action.payload?.transaction_id;

  if (action.action_type === "delete_transaction") {
    return `Операцію #${action.result?.deleted_transaction_id || action.payload?.transaction_id} видалено.`;
  }

  if (action.action_type === "update_transaction_sum") {
    return `Суму операції #${transactionId} оновлено, дані на сторінці оновлено.`;
  }

  if (action.action_type === "update_transaction_category") {
    return `Категорію операції #${transactionId} оновлено, дані на сторінці оновлено.`;
  }

  return transactionId
    ? `Операцію #${transactionId} застосовано, дані на сторінці оновлено.`
    : "Дію застосовано, дані на сторінці оновлено.";
}

export default function PendingActionCard({
  action,
  isProcessing,
  isDisabled,
  requiresAuthentication,
  onConfirm,
  onCancel,
}) {
  const status = statusLabels[action.status] ? action.status : "pending";
  const details = getActionDetails(action);

  return (
    <section
      aria-busy={isProcessing}
      aria-label={`Чернетка дії: ${actionLabels[action.action_type] || "Фінансова дія"}`}
      className={`pending-action pending-action--${status}`}
    >
      <div className="pending-action__header">
        <div>
          <span className="pending-action__eyebrow">Чернетка дії</span>
          <h3>{actionLabels[action.action_type] || "Фінансова дія"}</h3>
        </div>
        <span className="pending-action__status">{statusLabels[status]}</span>
      </div>

      {details.length > 0 ? (
        <dl className="pending-action__details">
          {details.map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      ) : null}

      {action.error ? (
        <p className="pending-action__error" role="alert">
          {action.error}
        </p>
      ) : null}

      {status === "pending" ? (
        <>
          {requiresAuthentication ? (
            <p className="pending-action__notice">
              Для підтвердження або відхилення увійдіть як адміністратор на вкладці
              створення операції.
            </p>
          ) : null}
          <div className="pending-action__actions">
            <button
              className="pending-action__button pending-action__button--confirm"
              disabled={isDisabled}
              type="button"
              onClick={onConfirm}
            >
              {isProcessing ? "Обробка..." : "Підтвердити"}
            </button>
            <button
              className="pending-action__button pending-action__button--cancel"
              disabled={isDisabled}
              type="button"
              onClick={onCancel}
            >
              Відхилити
            </button>
          </div>
        </>
      ) : null}

      {status === "confirmed" ? (
        <p className="pending-action__result">{confirmedMessage(action)}</p>
      ) : null}

      {status === "canceled" ? (
        <p className="pending-action__result">Дію відхилено. Дані не змінювалися.</p>
      ) : null}
    </section>
  );
}
