const tg = window.Telegram.WebApp;
const appConfig = window.APP_CONFIG || {};

tg.ready();
tg.expand();

const state = {
    initData: tg.initData || "",
    bootstrap: null,
    activeInvoiceId: null,
    invoicePollTimer: null,
};

const $ = (id) => document.getElementById(id);

const balanceValue = $("balance-value");
const userLabel = $("user-label");
const betAmount = $("bet-amount");
const betResult = $("bet-result");
const depositAmount = $("deposit-amount");
const depositButton = $("deposit-button");
const depositResult = $("deposit-result");
const depositMethodPill = $("deposit-method-pill");
const withdrawAmount = $("withdraw-amount");
const withdrawButton = $("withdraw-button");
const withdrawResult = $("withdraw-result");
const bonusesList = $("bonuses-list");
const promoList = $("promo-list");
const promoLimitPill = $("promo-limit-pill");
const promoCodeInput = $("promo-code-input");
const promoRedeemButton = $("promo-redeem-button");
const promoResult = $("promo-result");
const historyList = $("history-list");
const payoutMode = $("payout-mode");
const adminPanel = $("admin-panel");
const autoPayoutsToggle = $("auto-payouts-toggle");
const promoDailyLimitInput = $("promo-daily-limit-input");
const saveSettingsButton = $("save-settings");
const coreSettingsResult = $("core-settings-result");
const bonusForm = $("bonus-form");
const bonusAdminResult = $("bonus-admin-result");
const promoForm = $("promo-form");
const promoAdminResult = $("promo-admin-result");
const paymentForm = $("payment-form");
const paymentResult = $("payment-result");
const appSettingsForm = $("app-settings-form");
const appSettingsResult = $("app-settings-result");
const broadcastForm = $("broadcast-form");
const broadcastText = $("broadcast-text");
const broadcastButtons = $("broadcast-buttons");
const broadcastPreview = $("broadcast-preview");
const broadcastPreviewButton = $("broadcast-preview-button");
const broadcastResult = $("broadcast-result");
const broadcastRecipientCount = $("broadcast-recipient-count");
const broadcastHistory = $("broadcast-history");
const pendingWithdrawals = $("pending-withdrawals");
const adminBonusList = $("admin-bonus-list");
const adminPromoList = $("admin-promo-list");
const adminNotifications = $("admin-notifications");
const adminStats = $("admin-stats");
const adminLogList = $("admin-log-list");
const promoDepositRequiredToggle = $("promo-deposit-required-toggle");
const promoDepositDaysInput = $("promo-deposit-days-input");
const promoDepositAmountInput = $("promo-deposit-amount-input");
const depositsEnabledToggle = $("deposits-enabled-toggle");
const withdrawalsEnabledToggle = $("withdrawals-enabled-toggle");
const notificationsEnabledToggle = $("notifications-enabled-toggle");

async function api(path, options = {}) {
    const response = await fetch(path, {
        ...options,
        headers: {
            "X-Telegram-Init-Data": state.initData,
            ...(options.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
            ...(options.headers || {}),
        },
    });
    const data = await response.json();
    if (!response.ok || !data.ok) {
        throw new Error(data.error || "Ошибка запроса");
    }
    return data;
}

function escapeHtml(value) {
    return String(value || "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;");
}

function translateColor(color) {
    if (color === "red") return "красный";
    if (color === "black") return "черный";
    if (color === "green") return "зеленый";
    return color;
}

function setBalance(balance) {
    balanceValue.textContent = `${balance} ${appConfig.currency}`;
}

function applyVisualSettings(settings) {
    if (!settings) return;
    document.documentElement.style.setProperty("--accent", settings.primary_color || appConfig.primary_color || "#9d63ff");
    document.documentElement.style.setProperty("--accent-dark", settings.secondary_color || appConfig.secondary_color || "#5f35c7");
    document.documentElement.style.setProperty("--bg-glow", settings.accent_color || appConfig.accent_color || "#7ec8ff");
}

function renderHistory(items) {
    if (!items.length) {
        historyList.innerHTML = '<div class="history-item">Пока нет ставок.</div>';
        return;
    }
    historyList.innerHTML = items.map((item) => `
        <div class="history-item">
            <strong>${item.status === "win" ? "Выигрыш" : "Проигрыш"}</strong><br>
            Ставка: ${item.amount} ${appConfig.currency} на ${translateColor(item.color)}<br>
            Выпало: ${translateColor(item.winning_color)} (${item.winning_number})<br>
            Выплата: ${item.payout} ${appConfig.currency}
        </div>
    `).join("");
}

function renderBonuses(items) {
    if (!items.length) {
        bonusesList.innerHTML = '<div class="history-item">Бонусы пока не добавлены.</div>';
        return;
    }
    bonusesList.innerHTML = items.map((item) => {
        const image = item.image_path ? `<img src="${item.image_path}" alt="${escapeHtml(item.title)}">` : "";
        const button = item.claimed
            ? '<button class="action-btn small" type="button" disabled>Уже получен</button>'
            : `<button class="action-btn small" type="button" data-bonus-id="${item.id}">Получить бонус</button>`;
        return `
            <div class="bonus-card">
                ${image}
                <div class="bonus-top">
                    <div>
                        <h3 class="bonus-title">${escapeHtml(item.title)}</h3>
                        <div class="bonus-meta">Подписка на ${escapeHtml(item.channel_ref)}</div>
                    </div>
                    <div class="pill">${item.reward_amount} ${appConfig.currency}</div>
                </div>
                ${button}
            </div>
        `;
    }).join("");

    bonusesList.querySelectorAll("[data-bonus-id]").forEach((button) => {
        button.addEventListener("click", async () => {
            button.disabled = true;
            try {
                const data = await api(`/api/bonus/${button.dataset.bonusId}/claim`, {
                    method: "POST",
                    body: JSON.stringify({}),
                });
                setBalance(data.balance);
                tg.HapticFeedback.notificationOccurred("success");
                await loadBootstrap();
            } catch (error) {
                button.disabled = false;
                tg.showAlert(error.message);
            }
        });
    });
}

function renderPromos(promos) {
    const stats = promos?.stats || { daily_limit: 0, claimed_today: 0, remaining_today: 0 };
    const items = promos?.items || [];
    promoLimitPill.textContent = `Сегодня: ${stats.claimed_today}/${stats.daily_limit}, осталось ${stats.remaining_today}`;

    if (!items.length) {
        promoList.innerHTML = '<div class="history-item">Активных промокодов пока нет.</div>';
        return;
    }

    promoList.innerHTML = items.map((item) => {
        const status = item.claimed
            ? "Уже активирован тобой"
            : item.is_active
                ? `Осталось активаций: ${item.available_activations}`
                : "Промокод выключен";
        const conditions = item.deposit_required
            ? `Нужен депозит от ${item.deposit_amount} ${appConfig.currency} за ${item.deposit_days} дн.`
            : "Без обязательного депозита";
        return `
            <div class="history-item">
                <strong>${escapeHtml(item.title)}</strong><br>
                Код: ${escapeHtml(item.code)}<br>
                Награда: ${item.reward_amount} ${appConfig.currency}<br>
                ${conditions}<br>
                ${status}
            </div>
        `;
    }).join("");
}

function renderNotifications(items, enabled = true) {
    if (!enabled) {
        adminNotifications.innerHTML = '<div class="history-item">Уведомления отключены в настройках приложения.</div>';
        return;
    }
    if (!items?.length) {
        adminNotifications.innerHTML = '<div class="history-item">Новых уведомлений нет.</div>';
        return;
    }
    adminNotifications.innerHTML = items.map((item) => `
        <div class="history-item notice-${escapeHtml(item.level)}">${escapeHtml(item.text)}</div>
    `).join("");
}

function renderStats(stats) {
    if (!stats) {
        adminStats.innerHTML = "";
        return;
    }
    adminStats.innerHTML = [
        ["Пользователи", stats.users_total],
        ["Ставки", stats.bet_count],
        ["Пополнения", `${stats.deposit_sum} ${appConfig.currency}`],
        ["Выводы", `${stats.withdrawal_sum} ${appConfig.currency}`],
        ["Получатели рассылки", stats.broadcast_recipients],
    ].map(([label, value]) => `
        <div class="stat-card">
            <span class="stat-label">${label}</span>
            <strong>${value}</strong>
        </div>
    `).join("");
    broadcastRecipientCount.textContent = stats.broadcast_recipients || 0;
}

function renderBroadcasts(items) {
    if (!items?.length) {
        broadcastHistory.innerHTML = '<div class="history-item">Рассылок пока не было.</div>';
        return;
    }
    broadcastHistory.innerHTML = items.map((item) => `
        <div class="history-item">
            <strong>#${item.id}</strong> ${escapeHtml(item.status)}<br>
            ${escapeHtml(item.text_content).slice(0, 160)}<br>
            Получатели: ${item.recipient_count}<br>
            Создал: ${escapeHtml(item.created_by_label)}<br>
            Отправлено: ${escapeHtml(item.sent_at || item.created_at)}
        </div>
    `).join("");
}

function renderLogs(items) {
    if (!items?.length) {
        adminLogList.innerHTML = '<div class="history-item">Логи пока пустые.</div>';
        return;
    }
    adminLogList.innerHTML = items.map((item) => `
        <div class="history-item">
            <strong>${escapeHtml(item.action_type)}</strong><br>
            Админ: ${escapeHtml(item.admin_label)}<br>
            Секция: ${escapeHtml(item.target_type)} ${item.target_id ? `#${escapeHtml(item.target_id)}` : ""}<br>
            Детали: ${escapeHtml(JSON.stringify(item.details || {}))}<br>
            Время: ${escapeHtml(item.created_at)}
        </div>
    `).join("");
}

function renderAdmin(adminData, autoPayouts) {
    if (!state.bootstrap.user.is_admin) {
        adminPanel.classList.add("hidden");
        return;
    }

    adminPanel.classList.remove("hidden");
    autoPayoutsToggle.checked = autoPayouts;
    promoDailyLimitInput.value = adminData?.promo_daily_limit ?? "";

    const payment = adminData?.payment_settings || {};
    if (paymentForm) {
        paymentForm.method_title.value = payment.method_title || "CryptoBot";
        paymentForm.min_deposit_amount.value = payment.min_deposit_amount || "";
        paymentForm.max_deposit_amount.value = payment.max_deposit_amount || "";
        paymentForm.min_withdraw_amount.value = payment.min_withdraw_amount || "";
        paymentForm.max_withdraw_amount.value = payment.max_withdraw_amount || "";
        paymentForm.withdraw_fee_percent.value = payment.withdraw_fee_percent || "";
        depositsEnabledToggle.checked = !!payment.deposits_enabled;
        withdrawalsEnabledToggle.checked = !!payment.withdrawals_enabled;
    }

    const appSettings = adminData?.app_settings || {};
    if (appSettingsForm) {
        appSettingsForm.app_title.value = appSettings.app_title || "";
        appSettingsForm.app_subtitle.value = appSettings.app_subtitle || "";
        appSettingsForm.support_channel.value = appSettings.support_channel || "";
        appSettingsForm.telegram_link.value = appSettings.telegram_link || "";
        appSettingsForm.crypto_bot_link.value = appSettings.crypto_bot_link || "";
        appSettingsForm.landing_text.value = appSettings.landing_text || "";
        appSettingsForm.primary_color.value = appSettings.primary_color || "";
        appSettingsForm.secondary_color.value = appSettings.secondary_color || "";
        appSettingsForm.accent_color.value = appSettings.accent_color || "";
        notificationsEnabledToggle.checked = !!appSettings.notifications_enabled;
    }

    renderNotifications(adminData?.notifications || [], appSettings.notifications_enabled !== false);
    renderStats(adminData?.stats);
    renderBroadcasts(adminData?.broadcasts || []);
    renderLogs(adminData?.logs || []);

    const pendingItems = (adminData?.pending_withdrawals || []).map((item) => `
        <div class="history-item">
            <strong>#${item.id}</strong> ${escapeHtml(item.username || item.first_name || String(item.telegram_id))}<br>
            ${item.amount} ${appConfig.currency} · ${item.status}<br>
            <div class="admin-actions">
                <button class="action-btn small" type="button" data-withdraw-approve="${item.id}">Подтвердить</button>
                <button class="action-btn small secondary danger-btn" type="button" data-withdraw-reject="${item.id}">Отклонить</button>
            </div>
        </div>
    `).join("");
    pendingWithdrawals.innerHTML = pendingItems || '<div class="history-item">Нет активных заявок на вывод.</div>';

    const bonusItems = (adminData?.bonus_items || []).map((item) => `
        <div class="history-item">
            <strong>${escapeHtml(item.title)}</strong><br>
            ${escapeHtml(item.channel_ref)} · ${item.reward_amount} ${appConfig.currency}<br>
            Статус: ${item.is_active ? "включен" : "выключен"}
            <div class="admin-actions">
                <button class="action-btn small" type="button" data-bonus-toggle="${item.id}" data-next-state="${item.is_active ? "off" : "on"}">
                    ${item.is_active ? "Выключить" : "Включить"}
                </button>
                <button class="action-btn small secondary danger-btn" type="button" data-bonus-delete="${item.id}">Удалить</button>
            </div>
        </div>
    `).join("");
    adminBonusList.innerHTML = bonusItems || '<div class="history-item">Бонусов пока нет.</div>';

    const promoItems = (adminData?.promo_items || []).map((item) => `
        <div class="history-item">
            <strong>${escapeHtml(item.title)}</strong><br>
            Код: ${escapeHtml(item.code)} · ${item.reward_amount} ${appConfig.currency}<br>
            Активации: ${item.current_activations}/${item.max_activations}<br>
            ${item.deposit_required ? `Нужен депозит ${item.deposit_amount} ${appConfig.currency} за ${item.deposit_days} дн.<br>` : "Без депозита<br>"}
            Статус: ${item.is_active ? "включен" : "выключен"}
            <div class="admin-actions">
                <button class="action-btn small" type="button" data-promo-toggle="${item.id}" data-next-state="${item.is_active ? "off" : "on"}">
                    ${item.is_active ? "Выключить" : "Включить"}
                </button>
                <button class="action-btn small secondary danger-btn" type="button" data-promo-delete="${item.id}">Удалить</button>
            </div>
        </div>
    `).join("");
    adminPromoList.innerHTML = promoItems || '<div class="history-item">Промокодов пока нет.</div>';

    bindAdminActions();
}

function bindAdminActions() {
    pendingWithdrawals.querySelectorAll("[data-withdraw-approve]").forEach((button) => {
        button.addEventListener("click", () => resolveWithdrawal(button.dataset.withdrawApprove, true));
    });
    pendingWithdrawals.querySelectorAll("[data-withdraw-reject]").forEach((button) => {
        button.addEventListener("click", () => resolveWithdrawal(button.dataset.withdrawReject, false));
    });
    adminBonusList.querySelectorAll("[data-bonus-toggle]").forEach((button) => {
        button.addEventListener("click", () => toggleBonus(button.dataset.bonusToggle, button.dataset.nextState === "on"));
    });
    adminBonusList.querySelectorAll("[data-bonus-delete]").forEach((button) => {
        button.addEventListener("click", () => deleteBonus(button.dataset.bonusDelete));
    });
    adminPromoList.querySelectorAll("[data-promo-toggle]").forEach((button) => {
        button.addEventListener("click", () => togglePromo(button.dataset.promoToggle, button.dataset.nextState === "on"));
    });
    adminPromoList.querySelectorAll("[data-promo-delete]").forEach((button) => {
        button.addEventListener("click", () => deletePromo(button.dataset.promoDelete));
    });
}

function togglePromoDepositInputs() {
    const enabled = promoDepositRequiredToggle.checked;
    promoDepositDaysInput.disabled = !enabled;
    promoDepositAmountInput.disabled = !enabled;
    if (!enabled) {
        promoDepositDaysInput.value = "";
        promoDepositAmountInput.value = "";
    }
}

async function loadBootstrap() {
    const data = await api("/api/bootstrap");
    state.bootstrap = data;
    applyVisualSettings(data.app_settings);
    setBalance(data.user.balance);
    userLabel.textContent = data.user.username ? `@${data.user.username}` : data.user.first_name || `ID ${data.user.id}`;
    payoutMode.textContent = data.payments.auto_payouts ? "Автовыплаты включены" : "Ручная модерация";
    depositMethodPill.textContent = data.payments.method_title || "CryptoBot";
    renderHistory(data.recent_bets || []);
    renderBonuses(data.bonuses || []);
    renderPromos(data.promos || {});
    renderAdmin(data.admin, data.payments.auto_payouts);
}

async function placeBet(color) {
    try {
        const data = await api("/api/bet", {
            method: "POST",
            body: JSON.stringify({ color, amount: betAmount.value }),
        });
        setBalance(data.result.balance);
        const won = data.result.won;
        betResult.className = `result-card ${won ? "win" : "lose"}`;
        betResult.innerHTML = `
            Выпало <strong>${translateColor(data.result.winning_color)}</strong> (${data.result.winning_number})<br>
            Твоя ставка: ${translateColor(data.result.selected_color)}<br>
            Выплата: <strong>${data.result.payout} ${appConfig.currency}</strong><br>
            Баланс: <strong>${data.result.balance} ${appConfig.currency}</strong>
        `;
        tg.HapticFeedback.notificationOccurred(won ? "success" : "error");
        await loadBootstrap();
    } catch (error) {
        tg.showAlert(error.message);
    }
}

async function createDeposit() {
    try {
        const data = await api("/api/deposit", {
            method: "POST",
            body: JSON.stringify({ amount: depositAmount.value }),
        });
        state.activeInvoiceId = data.invoice.invoice_id;
        depositResult.innerHTML = `
            Счет создан.<br>
            <a href="${data.invoice.pay_url}" target="_blank" rel="noreferrer">Открыть оплату в CryptoBot</a>
        `;
        if (data.invoice.pay_url.includes("t.me")) {
            tg.openTelegramLink(data.invoice.pay_url);
        } else {
            tg.openLink(data.invoice.pay_url);
        }
        startInvoicePolling();
    } catch (error) {
        tg.showAlert(error.message);
    }
}

async function pollInvoice() {
    if (!state.activeInvoiceId) return;
    try {
        const data = await api(`/api/deposit/${state.activeInvoiceId}`);
        setBalance(data.balance);
        if (data.invoice.status === "paid") {
            depositResult.innerHTML = `Пополнение подтверждено. Баланс: ${data.balance} ${appConfig.currency}`;
            stopInvoicePolling();
            await loadBootstrap();
        } else {
            depositResult.innerHTML = `Статус счета: ${data.invoice.status}. Ожидаем оплату...`;
        }
    } catch (error) {
        stopInvoicePolling();
        depositResult.textContent = error.message;
    }
}

function startInvoicePolling() {
    stopInvoicePolling();
    state.invoicePollTimer = setInterval(pollInvoice, 5000);
    pollInvoice();
}

function stopInvoicePolling() {
    if (state.invoicePollTimer) {
        clearInterval(state.invoicePollTimer);
        state.invoicePollTimer = null;
    }
}

async function createWithdraw() {
    try {
        const data = await api("/api/withdraw", {
            method: "POST",
            body: JSON.stringify({ amount: withdrawAmount.value }),
        });
        setBalance(data.balance);
        withdrawResult.textContent = `Вывод #${data.result.withdrawal_id}: ${data.result.status}`;
        tg.HapticFeedback.notificationOccurred("success");
        await loadBootstrap();
    } catch (error) {
        tg.showAlert(error.message);
    }
}

async function redeemPromo() {
    try {
        const data = await api("/api/promo/redeem", {
            method: "POST",
            body: JSON.stringify({ code: promoCodeInput.value }),
        });
        setBalance(data.balance);
        promoResult.textContent = `Промокод активирован. Начислено ${data.reward} ${appConfig.currency}.`;
        promoCodeInput.value = "";
        tg.HapticFeedback.notificationOccurred("success");
        await loadBootstrap();
    } catch (error) {
        tg.showAlert(error.message);
        promoResult.textContent = error.message;
    }
}

async function saveAdminSettings() {
    try {
        await api("/api/admin/settings", {
            method: "POST",
            body: JSON.stringify({
                auto_payouts: autoPayoutsToggle.checked,
                promo_daily_limit: promoDailyLimitInput.value,
            }),
        });
        coreSettingsResult.textContent = "Базовые настройки сохранены.";
        await loadBootstrap();
    } catch (error) {
        tg.showAlert(error.message);
    }
}

async function resolveWithdrawal(withdrawalId, approve) {
    try {
        const data = await api(`/api/admin/withdrawals/${withdrawalId}/resolve`, {
            method: "POST",
            body: JSON.stringify({ approve }),
        });
        promoAdminResult.textContent = data.message;
        await loadBootstrap();
    } catch (error) {
        tg.showAlert(error.message);
    }
}

async function saveBonus(event) {
    event.preventDefault();
    const formData = new FormData(bonusForm);
    formData.append("initData", state.initData);
    formData.set("is_active", formData.get("is_active") ? "true" : "false");
    try {
        const data = await api("/api/admin/bonuses", {
            method: "POST",
            body: formData,
        });
        bonusForm.reset();
        bonusAdminResult.textContent = "Бонус сохранен.";
        renderBonuses(data.bonuses || []);
        await loadBootstrap();
    } catch (error) {
        bonusAdminResult.textContent = error.message;
    }
}

async function savePromo(event) {
    event.preventDefault();
    const formData = new FormData(promoForm);
    const payload = {
        title: formData.get("title"),
        code: formData.get("code"),
        reward_amount: formData.get("reward_amount"),
        max_activations: formData.get("max_activations"),
        deposit_required: !!formData.get("deposit_required"),
        deposit_days: formData.get("deposit_days"),
        deposit_amount: formData.get("deposit_amount"),
        is_active: !!formData.get("is_active"),
    };
    try {
        await api("/api/admin/promos", {
            method: "POST",
            body: JSON.stringify(payload),
        });
        promoForm.reset();
        togglePromoDepositInputs();
        promoAdminResult.textContent = "Промокод сохранен.";
        await loadBootstrap();
    } catch (error) {
        promoAdminResult.textContent = error.message;
    }
}

async function savePaymentSettings(event) {
    event.preventDefault();
    const formData = new FormData(paymentForm);
    formData.append("initData", state.initData);
    formData.set("deposits_enabled", formData.get("deposits_enabled") ? "true" : "false");
    formData.set("withdrawals_enabled", formData.get("withdrawals_enabled") ? "true" : "false");
    try {
        await api("/api/admin/payments", {
            method: "POST",
            body: formData,
        });
        paymentResult.textContent = "Платежные настройки сохранены.";
        await loadBootstrap();
    } catch (error) {
        paymentResult.textContent = error.message;
    }
}

async function saveAppSettings(event) {
    event.preventDefault();
    const formData = new FormData(appSettingsForm);
    formData.append("initData", state.initData);
    formData.set("notifications_enabled", formData.get("notifications_enabled") ? "true" : "false");
    try {
        await api("/api/admin/app-settings", {
            method: "POST",
            body: formData,
        });
        appSettingsResult.textContent = "Настройки приложения сохранены.";
        await loadBootstrap();
    } catch (error) {
        appSettingsResult.textContent = error.message;
    }
}

function renderBroadcastPreview() {
    const text = broadcastText.value.trim();
    let buttons = [];
    try {
        buttons = broadcastButtons.value.trim() ? JSON.parse(broadcastButtons.value.trim()) : [];
    } catch (error) {
        broadcastPreview.textContent = "Не удалось прочитать JSON кнопок.";
        return;
    }
    const buttonsMarkup = Array.isArray(buttons) && buttons.length
        ? buttons.map((item) => `<span class="pill neutral">${escapeHtml(item.text)} → ${escapeHtml(item.url)}</span>`).join(" ")
        : "<span class='bonus-meta'>Без кнопок</span>";
    broadcastPreview.innerHTML = `
        <strong>Предпросмотр рассылки</strong><br>
        ${escapeHtml(text || "Текст пока не заполнен")}<br><br>
        ${buttonsMarkup}
    `;
}

async function sendBroadcast(event) {
    event.preventDefault();
    const formData = new FormData(broadcastForm);
    formData.append("initData", state.initData);
    try {
        const data = await api("/api/admin/broadcasts/send", {
            method: "POST",
            body: formData,
        });
        broadcastResult.textContent = `Рассылка отправлена. Успешно: ${data.broadcast.sent}, ошибок: ${data.broadcast.failed}.`;
        broadcastForm.reset();
        renderBroadcastPreview();
        await loadBootstrap();
    } catch (error) {
        broadcastResult.textContent = error.message;
    }
}

async function toggleBonus(bonusId, isActive) {
    try {
        await api(`/api/admin/bonuses/${bonusId}/toggle`, {
            method: "POST",
            body: JSON.stringify({ is_active: isActive }),
        });
        await loadBootstrap();
    } catch (error) {
        tg.showAlert(error.message);
    }
}

async function deleteBonus(bonusId) {
    if (!window.confirm("Удалить бонус?")) return;
    try {
        await api(`/api/admin/bonuses/${bonusId}/delete`, {
            method: "POST",
            body: JSON.stringify({}),
        });
        await loadBootstrap();
    } catch (error) {
        tg.showAlert(error.message);
    }
}

async function togglePromo(promoId, isActive) {
    try {
        await api(`/api/admin/promos/${promoId}/toggle`, {
            method: "POST",
            body: JSON.stringify({ is_active: isActive }),
        });
        await loadBootstrap();
    } catch (error) {
        tg.showAlert(error.message);
    }
}

async function deletePromo(promoId) {
    if (!window.confirm("Удалить промокод?")) return;
    try {
        await api(`/api/admin/promos/${promoId}/delete`, {
            method: "POST",
            body: JSON.stringify({}),
        });
        await loadBootstrap();
    } catch (error) {
        tg.showAlert(error.message);
    }
}

document.querySelectorAll(".color-btn").forEach((button) => {
    button.addEventListener("click", () => placeBet(button.dataset.color));
});

depositButton.addEventListener("click", createDeposit);
withdrawButton.addEventListener("click", createWithdraw);
promoRedeemButton.addEventListener("click", redeemPromo);
saveSettingsButton.addEventListener("click", saveAdminSettings);
bonusForm.addEventListener("submit", saveBonus);
promoForm.addEventListener("submit", savePromo);
paymentForm.addEventListener("submit", savePaymentSettings);
appSettingsForm.addEventListener("submit", saveAppSettings);
broadcastForm.addEventListener("submit", sendBroadcast);
broadcastPreviewButton.addEventListener("click", renderBroadcastPreview);
promoDepositRequiredToggle.addEventListener("change", togglePromoDepositInputs);
broadcastText.addEventListener("input", renderBroadcastPreview);
broadcastButtons.addEventListener("input", renderBroadcastPreview);

togglePromoDepositInputs();
renderBroadcastPreview();

loadBootstrap().catch((error) => {
    betResult.textContent = error.message;
});
