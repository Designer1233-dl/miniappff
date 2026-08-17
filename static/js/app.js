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

const balanceValue = document.getElementById("balance-value");
const userLabel = document.getElementById("user-label");
const betAmount = document.getElementById("bet-amount");
const betResult = document.getElementById("bet-result");
const depositAmount = document.getElementById("deposit-amount");
const depositButton = document.getElementById("deposit-button");
const depositResult = document.getElementById("deposit-result");
const withdrawAmount = document.getElementById("withdraw-amount");
const withdrawButton = document.getElementById("withdraw-button");
const withdrawResult = document.getElementById("withdraw-result");
const bonusesList = document.getElementById("bonuses-list");
const promoList = document.getElementById("promo-list");
const promoLimitPill = document.getElementById("promo-limit-pill");
const promoCodeInput = document.getElementById("promo-code-input");
const promoRedeemButton = document.getElementById("promo-redeem-button");
const promoResult = document.getElementById("promo-result");
const historyList = document.getElementById("history-list");
const payoutMode = document.getElementById("payout-mode");
const adminPanel = document.getElementById("admin-panel");
const autoPayoutsToggle = document.getElementById("auto-payouts-toggle");
const promoDailyLimitInput = document.getElementById("promo-daily-limit-input");
const saveSettingsButton = document.getElementById("save-settings");
const bonusForm = document.getElementById("bonus-form");
const bonusAdminResult = document.getElementById("bonus-admin-result");
const promoForm = document.getElementById("promo-form");
const promoAdminResult = document.getElementById("promo-admin-result");
const pendingWithdrawals = document.getElementById("pending-withdrawals");
const adminBonusList = document.getElementById("admin-bonus-list");
const adminPromoList = document.getElementById("admin-promo-list");
const promoDepositRequiredToggle = document.getElementById("promo-deposit-required-toggle");
const promoDepositDaysInput = document.getElementById("promo-deposit-days-input");
const promoDepositAmountInput = document.getElementById("promo-deposit-amount-input");

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

function renderAdmin(adminData, autoPayouts) {
    if (!state.bootstrap.user.is_admin) {
        adminPanel.classList.add("hidden");
        return;
    }
    adminPanel.classList.remove("hidden");
    autoPayoutsToggle.checked = autoPayouts;
    promoDailyLimitInput.value = adminData?.promo_daily_limit ?? "";

    const items = (adminData?.pending_withdrawals || []).map((item) => `
        <div class="history-item">
            <strong>#${item.id}</strong> ${escapeHtml(item.username || item.first_name || String(item.telegram_id))}<br>
            ${item.amount} ${appConfig.currency} · ${item.status}<br>
            <div class="admin-actions">
                <button class="action-btn small" type="button" data-withdraw-approve="${item.id}">Подтвердить</button>
                <button class="action-btn small secondary danger-btn" type="button" data-withdraw-reject="${item.id}">Отклонить</button>
            </div>
        </div>
    `).join("");
    pendingWithdrawals.innerHTML = items || '<div class="history-item">Нет активных заявок на вывод.</div>';

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

    const promoItems = (adminData?.promo_items || []).map((item) => {
        const conditions = item.deposit_required
            ? `Нужен депозит ${item.deposit_amount} ${appConfig.currency} за ${item.deposit_days} дн.`
            : "Без депозита";
        return `
            <div class="history-item">
                <strong>${escapeHtml(item.title)}</strong><br>
                Код: ${escapeHtml(item.code)} · ${item.reward_amount} ${appConfig.currency}<br>
                Активации: ${item.current_activations}/${item.max_activations}<br>
                ${conditions}<br>
                Статус: ${item.is_active ? "включен" : "выключен"}
                <div class="admin-actions">
                    <button class="action-btn small" type="button" data-promo-toggle="${item.id}" data-next-state="${item.is_active ? "off" : "on"}">
                        ${item.is_active ? "Выключить" : "Включить"}
                    </button>
                    <button class="action-btn small secondary danger-btn" type="button" data-promo-delete="${item.id}">Удалить</button>
                </div>
            </div>
        `;
    }).join("");
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
    setBalance(data.user.balance);
    userLabel.textContent = data.user.username ? `@${data.user.username}` : data.user.first_name || `ID ${data.user.id}`;
    payoutMode.textContent = data.payments.auto_payouts ? "Автовыплаты включены" : "Ручная модерация";
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
        bonusAdminResult.textContent = "Настройки сохранены.";
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
promoDepositRequiredToggle.addEventListener("change", togglePromoDepositInputs);
togglePromoDepositInputs();

loadBootstrap().catch((error) => {
    betResult.textContent = error.message;
});
