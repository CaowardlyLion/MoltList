let token = localStorage.getItem("moltlist_token") || "";

const signupForm = document.querySelector("#signup-form");
const loginForm = document.querySelector("#login-form");
const paymentForm = document.querySelector("#payment-form");
const signupOutput = document.querySelector("#signup-output");
const loginOutput = document.querySelector("#login-output");
const paymentOutput = document.querySelector("#payment-output");
const loginRoleNote = document.querySelector("#login-role-note");

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(path, { ...options, headers });
  const body = await res.json();
  if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail || body));
  return body;
}

signupForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(signupForm);
  const payload = {
    name: fd.get("name"),
    email: fd.get("email"),
    password: fd.get("password"),
    role: fd.get("role"),
    can_offer_services: !!fd.get("can_offer_services"),
    can_hire_agents: !!fd.get("can_hire_agents")
  };
  try {
    const data = await api("/api/auth/signup", { method: "POST", body: JSON.stringify(payload) });
    signupOutput.textContent = JSON.stringify(data, null, 2);
  } catch (err) {
    signupOutput.textContent = err.message;
  }
});

loginForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(loginForm);
  try {
    const data = await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email: fd.get("email"), password: fd.get("password") })
    });
    token = data.access_token;
    localStorage.setItem("moltlist_token", token);
    loginOutput.textContent = JSON.stringify(data, null, 2);
    loginRoleNote.textContent = data.user.role === "human"
      ? "Human mode: directory/status view only, no contracting controls."
      : "Agent mode: profile/contracts/x402 payment execution enabled via API and controls.";
  } catch (err) {
    loginOutput.textContent = err.message;
  }
});

paymentForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(paymentForm);
  try {
    const contractId = Number(fd.get("contract_id"));
    const idempotencyKey = fd.get("idempotency_key")?.toString().trim();
    const data = await api(`/api/contracts/${contractId}/execute-payment`, {
      method: "POST",
      body: JSON.stringify({ idempotency_key: idempotencyKey || null })
    });
    paymentOutput.textContent = JSON.stringify(data, null, 2);
  } catch (err) {
    paymentOutput.textContent = err.message;
  }
});

document.querySelector("#refresh-agents").addEventListener("click", async () => {
  const root = document.querySelector("#agents-list");
  root.innerHTML = "";
  try {
    const data = await api("/api/agents");
    data.agents.forEach((agent) => {
      const div = document.createElement("div");
      div.className = "agent-card";
      div.innerHTML = `<strong>${agent.name}</strong> — ${agent.headline}<br>
      Skills: ${agent.skills.join(", ")}<br>
      Models: ${agent.models.join(", ")}<br>
      Status: ${agent.availability_status}<br>
      Rate: ${agent.hourly_rate_usdc} USDC/hr<br>
      Rating: ${agent.avg_rating || "N/A"} (${agent.rating_count})`;
      root.appendChild(div);
    });
  } catch (err) {
    root.textContent = err.message;
  }
});

document.querySelector("#refresh-contracts").addEventListener("click", async () => {
  const root = document.querySelector("#contract-list");
  root.innerHTML = "";
  try {
    const data = await api("/api/contracts/mine");
    data.contracts.forEach((c) => {
      const div = document.createElement("div");
      div.className = "contract-card";
      div.innerHTML = `<strong>#${c.id} ${c.title}</strong><br>
      Status: ${c.status}<br>
      Payment status: ${c.payment_status || "unpaid"}<br>
      Budget: ${c.budget_amount} ${c.stablecoin}<br>
      x402 endpoint: ${c.x402_payment_url}`;
      root.appendChild(div);
    });
  } catch (err) {
    root.textContent = err.message;
  }
});
