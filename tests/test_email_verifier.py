"""Tests for Hunter.io Verifier and Credit-Saver Policy."""

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.clock import FakeClock
from app.core.db import Base
from app.models.lead import RiskLevel
from app.models.setting import Setting
from app.services.dns_resolver import FakeDnsResolver
from app.services.email_verifier import HunterVerifier, VerifierResult
from app.services.validator import evaluate_email


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_credit_saver_policy_skips_external_verifier_on_local_bad(db_session):
    """If an email already fails local validation, Hunter should NEVER be called."""
    clock = FakeClock()
    dns = FakeDnsResolver(default_valid=True)
    mock_verifier = MagicMock()

    # 1. Syntax bad
    risk, _ = evaluate_email("bad-email", None, db_session, dns_resolver=dns, verifier=mock_verifier, clock=clock)
    assert risk == RiskLevel.BAD.value
    mock_verifier.verify.assert_not_called()

    # 2. Known typo domain (gail.com)
    mock_verifier.reset_mock()
    risk, _ = evaluate_email("user@gail.com", None, db_session, dns_resolver=dns, verifier=mock_verifier, clock=clock)
    assert risk == RiskLevel.BAD.value
    mock_verifier.verify.assert_not_called()

    # 3. Disposable domain
    mock_verifier.reset_mock()
    risk, _ = evaluate_email("temp@mailinator.com", None, db_session, dns_resolver=dns, verifier=mock_verifier, clock=clock)
    assert risk == RiskLevel.BAD.value
    mock_verifier.verify.assert_not_called()

    # 4. Dead DNS domain
    mock_verifier.reset_mock()
    dead_dns = FakeDnsResolver(default_valid=False)
    risk, _ = evaluate_email("john@deadcompany.com", None, db_session, dns_resolver=dead_dns, verifier=mock_verifier, clock=clock)
    assert risk == RiskLevel.BAD.value
    mock_verifier.verify.assert_not_called()


def test_credit_saver_calls_verifier_only_for_locally_valid(db_session):
    """When an email passes local checks, external verifier is queried."""
    clock = FakeClock()
    dns = FakeDnsResolver(default_valid=True)
    mock_verifier = MagicMock()
    mock_verifier.verify.return_value = VerifierResult(
        status="valid", risk_level=RiskLevel.OK.value, raw={}
    )

    risk, _ = evaluate_email("clean@validcompany.com", None, db_session, dns_resolver=dns, verifier=mock_verifier, clock=clock)
    assert risk == RiskLevel.OK.value
    mock_verifier.verify.assert_called_once_with("clean@validcompany.com", db_session, clock)


def test_hunter_quota_exhausted_fallback_and_alert(db_session):
    """When quota is reached or 429 received, fallback activates and persistent alert is recorded."""
    clock = FakeClock()
    dns = FakeDnsResolver(default_valid=True)

    verifier = HunterVerifier(api_key="mock_key", monthly_quota=2)

    # Mock HTTP 429 response
    mock_response = MagicMock()
    mock_response.status_code = 429

    with patch("httpx.Client.get", return_value=mock_response):
        res = verifier.verify("lead1@domain.com", db_session, clock)
        # Should return None so system falls back to local evaluation
        assert res is None

        # Verify persistent alert flag is set in settings
        flag = db_session.scalar(select(Setting.value).where(Setting.key == "HUNTER_QUOTA_EXCEEDED"))
        assert flag == "true"
        dismissed = db_session.scalar(select(Setting.value).where(Setting.key == "HUNTER_QUOTA_ALERT_DISMISSED"))
        assert dismissed == "false"

    # evaluate_email continues seamlessly with local fallback
    risk, reason = evaluate_email("lead2@domain.com", None, db_session, dns_resolver=dns, verifier=verifier, clock=clock)
    assert risk == RiskLevel.OK.value
    assert "Passed all validation checks" in reason
