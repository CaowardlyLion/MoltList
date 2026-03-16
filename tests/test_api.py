from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.main import app


def test_signup_login_contract_rating_and_x402_execution():
    db_path = Path("moltlist.db")
    if db_path.exists():
        db_path.unlink()

    with TestClient(app) as client:
        provider = client.post(
            "/api/auth/signup",
            json={
                "email": "provider@example.com",
                "name": "Provider",
                "password": "password123",
                "role": "agent",
                "can_offer_services": True,
                "can_hire_agents": False,
            },
        )
        assert provider.status_code == 200
        provider_id = provider.json()["user"]["id"]

        contractor = client.post(
            "/api/auth/signup",
            json={
                "email": "contractor@example.com",
                "name": "Contractor",
                "password": "password123",
                "role": "agent",
                "can_offer_services": False,
                "can_hire_agents": True,
            },
        )
        assert contractor.status_code == 200

        human = client.post(
            "/api/auth/signup",
            json={
                "email": "human@example.com",
                "name": "Human",
                "password": "password123",
                "role": "human",
                "can_offer_services": True,
                "can_hire_agents": False,
            },
        )
        assert human.status_code == 400

        provider_login = client.post(
            "/api/auth/login", json={"email": "provider@example.com", "password": "password123"}
        )
        provider_token = provider_login.json()["access_token"]

        profile = client.post(
            "/api/agents/profile",
            headers={"Authorization": f"Bearer {provider_token}"},
            json={
                "headline": "Automation specialist",
                "skills": ["scraping", "summarization"],
                "models": ["gpt-5.2", "llama-3"],
                "hourly_rate_usdc": 25,
                "bio": "I automate cross-system workflows",
                "availability_status": "available",
            },
        )
        assert profile.status_code == 200

        contractor_login = client.post(
            "/api/auth/login", json={"email": "contractor@example.com", "password": "password123"}
        )
        contractor_token = contractor_login.json()["access_token"]

        contract = client.post(
            "/api/contracts",
            headers={"Authorization": f"Bearer {contractor_token}"},
            json={
                "hired_agent_id": provider_id,
                "title": "Build lead list",
                "description": "Gather 100 verified leads",
                "stablecoin": "USDC",
                "budget_amount": 120,
                "x402_payment_url": "https://x402.example/pay/provider",
            },
        )
        assert contract.status_code == 200
        contract_id = contract.json()["contract"]["id"]

        activate = client.patch(
            f"/api/contracts/{contract_id}/status",
            headers={"Authorization": f"Bearer {contractor_token}"},
            json={"status": "active"},
        )
        assert activate.status_code == 200

        ok_response = Mock()
        ok_response.status_code = 200
        ok_response.content = b'{"accepted":true,"tx_ref":"mock_tx_abc"}'
        ok_response.json.return_value = {"accepted": True, "tx_ref": "mock_tx_abc"}

        with patch("app.main.httpx.post", return_value=ok_response):
            execute = client.post(
                f"/api/contracts/{contract_id}/execute-payment",
                headers={"Authorization": f"Bearer {contractor_token}"},
                json={},
            )
        assert execute.status_code == 200
        assert execute.json()["payment"]["status"] == "paid"

        payments = client.get(
            f"/api/contracts/{contract_id}/payments",
            headers={"Authorization": f"Bearer {provider_token}"},
        )
        assert payments.status_code == 200
        assert len(payments.json()["payments"]) == 1

        rate_1 = client.post(
            f"/api/contracts/{contract_id}/rating",
            headers={"Authorization": f"Bearer {contractor_token}"},
            json={"stars": 5, "comment": "Great delivery"},
        )
        assert rate_1.status_code == 200

        rate_2 = client.post(
            f"/api/contracts/{contract_id}/rating",
            headers={"Authorization": f"Bearer {provider_token}"},
            json={"stars": 5, "comment": "Great client"},
        )
        assert rate_2.status_code == 200

        failing_contract = client.post(
            "/api/contracts",
            headers={"Authorization": f"Bearer {contractor_token}"},
            json={
                "hired_agent_id": provider_id,
                "title": "Failing payment contract",
                "description": "Expected to fail x402 execution",
                "stablecoin": "USDC",
                "budget_amount": 10,
                "x402_payment_url": "https://x402.example/pay/failing",
            },
        )
        failing_contract_id = failing_contract.json()["contract"]["id"]
        client.patch(
            f"/api/contracts/{failing_contract_id}/status",
            headers={"Authorization": f"Bearer {contractor_token}"},
            json={"status": "active"},
        )

        bad_response = Mock()
        bad_response.status_code = 402
        bad_response.content = b'{"accepted":false,"error":"insufficient funds"}'
        bad_response.json.return_value = {"accepted": False, "error": "insufficient funds"}

        with patch("app.main.httpx.post", return_value=bad_response):
            fail_execute = client.post(
                f"/api/contracts/{failing_contract_id}/execute-payment",
                headers={"Authorization": f"Bearer {contractor_token}"},
                json={},
            )
        assert fail_execute.status_code == 502

        agents = client.get("/api/agents")
        assert agents.status_code == 200
        assert len(agents.json()["agents"]) >= 1
