from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from app.main import SCHEMA_REVISION, create_app


def test_database_outage_preserves_liveness_and_hides_details():
    engine = MagicMock(spec=Engine)
    engine.connect.side_effect = OperationalError("secret-host/password", {}, Exception("secret"))
    with TestClient(create_app(engine)) as client:
        assert client.get("/api/health").status_code == 200
        response = client.get("/api/ready")
        assert response.status_code == 503
        assert response.json() == {
            "status": "unavailable",
            "database": "unavailable",
            "schema_status": "unknown",
        }
        assert "secret" not in response.text
        assert client.get("/api/v1/domains").status_code == 503


def test_old_schema_is_not_ready():
    engine = MagicMock(spec=Engine)
    connection = engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.scalar.return_value = "old_revision"
    with TestClient(create_app(engine)) as client:
        response = client.get("/api/ready")
        assert response.status_code == 503
        assert response.json()["schema_status"] == "pending"
        connection.execute.return_value.scalar.return_value = SCHEMA_REVISION
        assert client.get("/api/ready").json()["status"] == "ok"
