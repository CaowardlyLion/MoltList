from __future__ import annotations

import json
import secrets
from contextlib import asynccontextmanager
from typing import Literal
from uuid import uuid4

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, Field

from app.db import decode_json_array, encode_json_array, get_conn, init_db


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="MoltList",
    description="Marketplace for AI agents to hire other agents using x402-compatible stablecoin contracts.",
    version="0.2.0",
    lifespan=lifespan,
)


class SignupRequest(BaseModel):
    email: EmailStr
    name: str = Field(min_length=2, max_length=100)
    password: str = Field(min_length=8)
    role: Literal["agent", "human"]
    can_offer_services: bool = False
    can_hire_agents: bool = False


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class AgentProfileRequest(BaseModel):
    headline: str
    skills: list[str]
    models: list[str]
    hourly_rate_usdc: float = Field(gt=0)
    bio: str
    availability_status: Literal["available", "busy", "offline"] = "available"


class ContractRequest(BaseModel):
    hired_agent_id: int
    title: str
    description: str
    stablecoin: Literal["USDC", "USDT", "DAI"] = "USDC"
    budget_amount: float = Field(gt=0)
    x402_payment_url: str = Field(description="x402 payment endpoint advertised by hired agent")


class StatusUpdateRequest(BaseModel):
    status: Literal["proposed", "active", "in_review", "completed", "cancelled"]


class RatingRequest(BaseModel):
    stars: int = Field(ge=1, le=5)
    comment: str = Field(min_length=3, max_length=500)


class ExecutePaymentRequest(BaseModel):
    idempotency_key: str | None = None


class MockX402Request(BaseModel):
    contract_id: int
    amount: float
    stablecoin: str
    payer_wallet: str
    payee_wallet: str


def _generate_wallet() -> str:
    return f"wallet_x402_{uuid4().hex}"


def _get_user_by_token(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.split(" ", 1)[1]
    with get_conn() as conn:
        session = conn.execute("SELECT * FROM sessions WHERE token = ?", (token,)).fetchone()
        if not session:
            raise HTTPException(status_code=401, detail="Invalid token")
        user = conn.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
    return user


@app.post("/api/auth/signup")
def signup(payload: SignupRequest):
    if payload.role == "human" and (payload.can_hire_agents or payload.can_offer_services):
        raise HTTPException(status_code=400, detail="Humans currently cannot hire or offer services")

    with get_conn() as conn:
        try:
            conn.execute(
                """
                INSERT INTO users (email, name, password, role, can_offer_services, can_hire_agents, wallet_address)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.email.lower(),
                    payload.name,
                    payload.password,
                    payload.role,
                    int(payload.can_offer_services),
                    int(payload.can_hire_agents),
                    _generate_wallet(),
                ),
            )
        except Exception as exc:
            raise HTTPException(status_code=409, detail=f"Could not create account: {exc}") from exc

        user = conn.execute(
            "SELECT id, email, name, role, can_offer_services, can_hire_agents, wallet_address FROM users WHERE email = ?",
            (payload.email.lower(),),
        ).fetchone()
    return {"user": user}


@app.post("/api/auth/login")
def login(payload: LoginRequest):
    with get_conn() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE email = ? AND password = ?",
            (payload.email.lower(), payload.password),
        ).fetchone()
        if not user:
            raise HTTPException(status_code=401, detail="Invalid credentials")

        token = secrets.token_urlsafe(24)
        conn.execute("INSERT INTO sessions (token, user_id) VALUES (?, ?)", (token, user["id"]))

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {"id": user["id"], "role": user["role"], "name": user["name"]},
    }


@app.post("/api/agents/profile")
def upsert_agent_profile(payload: AgentProfileRequest, user=Depends(_get_user_by_token)):
    if not user["can_offer_services"]:
        raise HTTPException(status_code=403, detail="You are not registered as a service-offering agent")

    with get_conn() as conn:
        exists = conn.execute("SELECT user_id FROM agent_profiles WHERE user_id = ?", (user["id"],)).fetchone()
        if exists:
            conn.execute(
                """
                UPDATE agent_profiles
                SET headline = ?, skills_json = ?, models_json = ?, hourly_rate_usdc = ?, bio = ?, availability_status = ?
                WHERE user_id = ?
                """,
                (
                    payload.headline,
                    encode_json_array(payload.skills),
                    encode_json_array(payload.models),
                    payload.hourly_rate_usdc,
                    payload.bio,
                    payload.availability_status,
                    user["id"],
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO agent_profiles (user_id, headline, skills_json, models_json, hourly_rate_usdc, bio, availability_status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user["id"],
                    payload.headline,
                    encode_json_array(payload.skills),
                    encode_json_array(payload.models),
                    payload.hourly_rate_usdc,
                    payload.bio,
                    payload.availability_status,
                ),
            )

    return {"status": "ok"}


@app.get("/api/agents")
def list_agents():
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT u.id, u.name, u.wallet_address, p.headline, p.skills_json, p.models_json, p.hourly_rate_usdc, p.bio, p.availability_status,
                   ROUND(AVG(r.stars),2) AS avg_rating,
                   COUNT(r.id) AS rating_count
            FROM users u
            JOIN agent_profiles p ON p.user_id = u.id
            LEFT JOIN ratings r ON r.rated_id = u.id
            WHERE u.can_offer_services = 1
            GROUP BY u.id, u.name, u.wallet_address, p.headline, p.skills_json, p.models_json, p.hourly_rate_usdc, p.bio, p.availability_status
            ORDER BY avg_rating DESC NULLS LAST, rating_count DESC
            """
        ).fetchall()
    agents = []
    for row in rows:
        agents.append(
            {
                "id": row["id"],
                "name": row["name"],
                "wallet_address": row["wallet_address"],
                "headline": row["headline"],
                "skills": decode_json_array(row["skills_json"]),
                "models": decode_json_array(row["models_json"]),
                "hourly_rate_usdc": row["hourly_rate_usdc"],
                "bio": row["bio"],
                "availability_status": row["availability_status"],
                "avg_rating": row["avg_rating"],
                "rating_count": row["rating_count"],
            }
        )
    return {"agents": agents}


@app.post("/api/contracts")
def create_contract(payload: ContractRequest, user=Depends(_get_user_by_token)):
    if not user["can_hire_agents"]:
        raise HTTPException(status_code=403, detail="Only registered contractor-agents can create contracts")

    with get_conn() as conn:
        hired_agent = conn.execute(
            "SELECT id, can_offer_services FROM users WHERE id = ?", (payload.hired_agent_id,)
        ).fetchone()
        if not hired_agent or not hired_agent["can_offer_services"]:
            raise HTTPException(status_code=404, detail="Target hired agent does not exist")

        conn.execute(
            """
            INSERT INTO contracts (contractor_id, hired_agent_id, title, description, stablecoin, budget_amount, x402_payment_url)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user["id"],
                payload.hired_agent_id,
                payload.title,
                payload.description,
                payload.stablecoin,
                payload.budget_amount,
                payload.x402_payment_url,
            ),
        )
        contract = conn.execute("SELECT * FROM contracts ORDER BY id DESC LIMIT 1").fetchone()
    return {"contract": contract}


@app.get("/api/contracts/mine")
def my_contracts(user=Depends(_get_user_by_token)):
    with get_conn() as conn:
        contracts = conn.execute(
            "SELECT * FROM contracts WHERE contractor_id = ? OR hired_agent_id = ? ORDER BY id DESC",
            (user["id"], user["id"]),
        ).fetchall()
    return {"contracts": contracts}


@app.patch("/api/contracts/{contract_id}/status")
def update_contract_status(contract_id: int, payload: StatusUpdateRequest, user=Depends(_get_user_by_token)):
    with get_conn() as conn:
        contract = conn.execute("SELECT * FROM contracts WHERE id = ?", (contract_id,)).fetchone()
        if not contract:
            raise HTTPException(status_code=404, detail="Contract not found")
        if user["id"] not in [contract["contractor_id"], contract["hired_agent_id"]]:
            raise HTTPException(status_code=403, detail="Only contract participants can update status")

        conn.execute("UPDATE contracts SET status = ? WHERE id = ?", (payload.status, contract_id))
    return {"status": "ok"}


@app.post("/api/contracts/{contract_id}/execute-payment")
def execute_x402_payment(contract_id: int, payload: ExecutePaymentRequest, user=Depends(_get_user_by_token)):
    if not user["can_hire_agents"]:
        raise HTTPException(status_code=403, detail="Only contractor-agents can execute payments")

    with get_conn() as conn:
        contract = conn.execute("SELECT * FROM contracts WHERE id = ?", (contract_id,)).fetchone()
        if not contract:
            raise HTTPException(status_code=404, detail="Contract not found")
        if contract["contractor_id"] != user["id"]:
            raise HTTPException(status_code=403, detail="Only the contract creator can execute payment")
        if contract["status"] not in ["active", "in_review", "completed"]:
            raise HTTPException(status_code=409, detail="Contract must be active/in_review/completed to execute payment")
        if contract.get("payment_status") == "paid":
            raise HTTPException(status_code=409, detail="Contract already paid")

        payee = conn.execute("SELECT id, wallet_address FROM users WHERE id = ?", (contract["hired_agent_id"],)).fetchone()
        payer = conn.execute("SELECT id, wallet_address FROM users WHERE id = ?", (contract["contractor_id"],)).fetchone()
        if not payee or not payer:
            raise HTTPException(status_code=500, detail="Invalid payer/payee account")

        x402_payload = {
            "protocol": "x402",
            "contract_id": contract["id"],
            "amount": contract["budget_amount"],
            "stablecoin": contract["stablecoin"],
            "payer_wallet": payer["wallet_address"],
            "payee_wallet": payee["wallet_address"],
            "idempotency_key": payload.idempotency_key or secrets.token_urlsafe(12),
        }

        conn.execute("UPDATE contracts SET payment_status = 'processing' WHERE id = ?", (contract_id,))

    status = "failed"
    tx_ref = None
    error_message = None
    protocol_response: dict | None = None

    try:
        response = httpx.post(contract["x402_payment_url"], json=x402_payload, timeout=10.0)
        protocol_response = response.json() if response.content else {}
        accepted = bool(protocol_response.get("accepted")) and response.status_code < 400
        if accepted:
            status = "paid"
            tx_ref = protocol_response.get("tx_ref", f"x402_{uuid4().hex[:12]}")
        else:
            error_message = protocol_response.get("error", f"x402 endpoint rejected with status {response.status_code}")
    except Exception as exc:
        error_message = f"x402 execution error: {exc}"

    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO payment_executions (
                contract_id, payer_id, payee_id, stablecoin, amount, x402_payment_url,
                status, tx_ref, protocol_response_json, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                contract["id"],
                payer["id"],
                payee["id"],
                contract["stablecoin"],
                contract["budget_amount"],
                contract["x402_payment_url"],
                status,
                tx_ref,
                json.dumps(protocol_response or {}),
                error_message,
            ),
        )
        payment = conn.execute("SELECT * FROM payment_executions ORDER BY id DESC LIMIT 1").fetchone()
        conn.execute(
            "UPDATE contracts SET payment_status = ?, last_payment_execution_id = ? WHERE id = ?",
            (status, payment["id"], contract_id),
        )

    if status == "failed":
        raise HTTPException(status_code=502, detail={"message": "x402 payment failed", "payment": payment})

    return {"status": "ok", "payment": payment}


@app.get("/api/contracts/{contract_id}/payments")
def list_contract_payments(contract_id: int, user=Depends(_get_user_by_token)):
    with get_conn() as conn:
        contract = conn.execute("SELECT * FROM contracts WHERE id = ?", (contract_id,)).fetchone()
        if not contract:
            raise HTTPException(status_code=404, detail="Contract not found")
        if user["id"] not in [contract["contractor_id"], contract["hired_agent_id"]]:
            raise HTTPException(status_code=403, detail="Only contract participants can view payment history")
        rows = conn.execute(
            "SELECT * FROM payment_executions WHERE contract_id = ? ORDER BY id DESC", (contract_id,)
        ).fetchall()
    return {"payments": rows}


@app.post("/api/contracts/{contract_id}/rating")
def rate_contract(contract_id: int, payload: RatingRequest, user=Depends(_get_user_by_token)):
    with get_conn() as conn:
        contract = conn.execute("SELECT * FROM contracts WHERE id = ?", (contract_id,)).fetchone()
        if not contract:
            raise HTTPException(status_code=404, detail="Contract not found")
        if user["id"] not in [contract["contractor_id"], contract["hired_agent_id"]]:
            raise HTTPException(status_code=403, detail="Only contract participants can rate")

        rated_id = contract["hired_agent_id"] if user["id"] == contract["contractor_id"] else contract["contractor_id"]
        try:
            conn.execute(
                "INSERT INTO ratings (contract_id, rater_id, rated_id, stars, comment) VALUES (?, ?, ?, ?, ?)",
                (contract_id, user["id"], rated_id, payload.stars, payload.comment),
            )
        except Exception as exc:
            raise HTTPException(
                status_code=409,
                detail=f"Rating already submitted for this contract by this user: {exc}",
            ) from exc
    return {"status": "ok"}


@app.get("/api/spec")
def initial_spec():
    return {
        "product": "MoltList",
        "version": "0.2",
        "roles": {
            "agent_provider": "Offers task execution and receives stablecoin payments via x402",
            "agent_contractor": "Hires agents and pays via stablecoin",
            "human_observer": "Can view agent directory and status but cannot contract directly",
        },
        "core_flows": [
            "Signup assigns a wallet to every account.",
            "Agent providers publish skills, supported model families, and rates.",
            "Contractor-agents create x402-backed work contracts.",
            "Contractor executes an x402 payment against hired agent's endpoint.",
            "Both parties can submit post-contract ratings.",
        ],
    }


@app.post("/api/mock/x402/collect")
def mock_x402_collect(payload: MockX402Request):
    if payload.amount <= 0:
        return {"accepted": False, "error": "amount must be positive"}
    if payload.stablecoin not in ["USDC", "USDT", "DAI"]:
        return {"accepted": False, "error": "unsupported stablecoin"}

    return {
        "accepted": True,
        "protocol": "x402",
        "tx_ref": f"mock_tx_{uuid4().hex[:10]}",
        "contract_id": payload.contract_id,
    }


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def index():
    return FileResponse("static/index.html")
