"""Wave 2 redaction tests — P0-12.

Container-first: run via:
  docker compose run --rm crystalium pytest mcp-server/tests/test_redaction.py -v

Coverage:
  - SSN, AWS key, GitHub token, JWT, PRIVATE KEY, password patterns
  - redact(sensitivity_tag='none') only fires PII rules
  - redact(sensitivity_tag='secrets') fires all rules
  - redact_for_handoff(from_tier=T1, to_tier=T3) applies max-redaction
  - redact_for_handoff(from_tier=T1, to_tier=T0) skips extra redaction (more trusted)
  - Replacement sentinel format: «REDACTED:<rule_id>»
  - Deep dict/list redaction in redact_for_handoff
"""

from __future__ import annotations

import pytest

from crystalium.aetheryte.redact import Redactor, REDACTION_PATTERNS
from crystalium.config import Config
from crystalium.trust import Tier


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def redactor() -> Redactor:
    return Redactor(Config())


# ---------------------------------------------------------------------------
# Sentinel format
# ---------------------------------------------------------------------------


class TestSentinelFormat:
    def test_ssn_replaced_with_sentinel(self, redactor: Redactor) -> None:
        text = "Patient SSN: 123-45-6789"
        result = redactor.redact(text, "pii")
        assert "123-45-6789" not in result
        assert "«REDACTED:SSN»" in result

    def test_sentinel_format(self) -> None:
        """Sentinel uses guillemets: «REDACTED:<rule_id>»."""
        # Use a SSN not excluded by the pattern's negative lookaheads:
        # pattern excludes 000-xx, 666-xx, 9xx-xx prefixes.
        result = Redactor(Config()).redact("SSN: 123-45-6789", "pii")
        assert result.startswith("SSN: «REDACTED:SSN»")


# ---------------------------------------------------------------------------
# SSN pattern
# ---------------------------------------------------------------------------


class TestSSNPattern:
    def test_ssn_dashes(self, redactor: Redactor) -> None:
        assert "123-45-6789" not in redactor.redact("123-45-6789", "pii")

    def test_ssn_spaces(self, redactor: Redactor) -> None:
        assert "123 45 6789" not in redactor.redact("123 45 6789", "pii")

    def test_ssn_no_separator(self, redactor: Redactor) -> None:
        # 9-digit SSN without separators
        assert "123456789" not in redactor.redact("123456789", "pii")

    def test_ssn_not_triggered_for_none_sensitivity(self, redactor: Redactor) -> None:
        """sensitivity_tag='none' should still fire PII rules (SSN)."""
        # Actually per spec: sensitivity_tag='none' fires PII regex
        text = "SSN: 123-45-6789"
        result = redactor.redact(text, "none")
        # SSN IS in PII rules, so it should be redacted even for 'none'
        assert "123-45-6789" not in result


# ---------------------------------------------------------------------------
# AWS key pattern
# ---------------------------------------------------------------------------


class TestAWSKeyPattern:
    def test_aws_akia_key_redacted(self, redactor: Redactor) -> None:
        key = "AKIAIOSFODNN7EXAMPLE"
        result = redactor.redact(f"AWS_ACCESS_KEY_ID={key}", "secrets")
        assert key not in result
        assert "«REDACTED:AWS_KEY»" in result

    def test_aws_asia_key_redacted(self, redactor: Redactor) -> None:
        key = "ASIAIOSFODNN7EXAMPLE"
        result = redactor.redact(f"key={key}", "secrets")
        assert key not in result

    def test_aws_key_not_redacted_for_none_sensitivity(self, redactor: Redactor) -> None:
        """AWS keys are secrets rules; sensitivity_tag='none' should NOT fire AWS_KEY."""
        key = "AKIAIOSFODNN7EXAMPLE"
        result = redactor.redact(f"AWS_ACCESS_KEY_ID={key}", "none")
        # AWS_KEY is in _SECRETS_RULES, not _PII_RULES → not fired for 'none'
        assert key in result, "AWS key should NOT be redacted for sensitivity_tag='none'"


# ---------------------------------------------------------------------------
# GitHub token pattern
# ---------------------------------------------------------------------------


class TestGitHubTokenPattern:
    def test_ghp_token_redacted(self, redactor: Redactor) -> None:
        token = "ghp_" + "A" * 36
        result = redactor.redact(f"token = {token}", "secrets")
        assert token not in result
        assert "«REDACTED:GITHUB_TOKEN»" in result

    def test_gho_token_redacted(self, redactor: Redactor) -> None:
        token = "gho_" + "B" * 36
        result = redactor.redact(token, "secrets")
        assert token not in result

    def test_github_pat_token_redacted(self, redactor: Redactor) -> None:
        token = "github_pat_" + "C" * 36
        result = redactor.redact(token, "secrets")
        assert token not in result


# ---------------------------------------------------------------------------
# JWT pattern
# ---------------------------------------------------------------------------


class TestJWTPattern:
    def test_jwt_shape_redacted(self, redactor: Redactor) -> None:
        # Realistic JWT-like token (3 base64url segments)
        header = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        payload_part = "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4ifQ"
        signature = "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        jwt = f"{header}.{payload_part}.{signature}"

        result = redactor.redact(f"Authorization: Bearer {jwt}", "secrets")
        assert jwt not in result

    def test_short_strings_not_flagged_as_jwt(self, redactor: Redactor) -> None:
        """Strings shorter than 20 chars per segment should not be flagged."""
        short = "abc.def.ghi"
        result = redactor.redact(short, "secrets")
        assert short in result  # not redacted (too short)


# ---------------------------------------------------------------------------
# PRIVATE KEY pattern
# ---------------------------------------------------------------------------


class TestPrivateKeyPattern:
    def test_rsa_private_key_header_redacted(self, redactor: Redactor) -> None:
        text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow..."
        result = redactor.redact(text, "secrets")
        assert "BEGIN RSA PRIVATE KEY" not in result

    def test_ec_private_key_header_redacted(self, redactor: Redactor) -> None:
        text = "-----BEGIN EC PRIVATE KEY-----"
        result = redactor.redact(text, "secrets")
        assert "BEGIN EC PRIVATE KEY" not in result

    def test_generic_private_key_header_redacted(self, redactor: Redactor) -> None:
        text = "-----BEGIN PRIVATE KEY-----"
        result = redactor.redact(text, "secrets")
        assert "BEGIN PRIVATE KEY" not in result


# ---------------------------------------------------------------------------
# Password pattern
# ---------------------------------------------------------------------------


class TestPasswordPattern:
    def test_password_colon_redacted(self, redactor: Redactor) -> None:
        result = redactor.redact("password: s3cr3t123", "secrets")
        assert "s3cr3t123" not in result
        assert "«REDACTED:PASSWORD»" in result

    def test_password_equals_redacted(self, redactor: Redactor) -> None:
        result = redactor.redact("PASSWORD=my_secret_value", "secrets")
        assert "my_secret_value" not in result

    def test_passwd_redacted(self, redactor: Redactor) -> None:
        result = redactor.redact("passwd: hunter2", "secrets")
        assert "hunter2" not in result

    def test_pass_redacted(self, redactor: Redactor) -> None:
        result = redactor.redact("pass=openSesame!", "secrets")
        assert "openSesame!" not in result

    def test_password_case_insensitive(self, redactor: Redactor) -> None:
        result = redactor.redact("PASSWORD: Top$ecret", "secrets")
        assert "Top$ecret" not in result


# ---------------------------------------------------------------------------
# Sensitivity tag behavior
# ---------------------------------------------------------------------------


class TestSensitivityTagBehavior:
    def test_none_tag_only_pii_rules(self, redactor: Redactor) -> None:
        """sensitivity_tag='none' fires only PII rules (SSN); secrets rules skip."""
        text = "SSN: 234-56-7890 and AKIAIOSFODNN7EXAMPLE"
        result = redactor.redact(text, "none")
        assert "234-56-7890" not in result  # SSN (PII rule) — should be redacted
        assert "AKIAIOSFODNN7EXAMPLE" in result  # AWS key (secrets rule) — skipped

    def test_pii_tag_fires_all_rules(self, redactor: Redactor) -> None:
        """sensitivity_tag='pii' fires ALL rules (PII + secrets)."""
        text = "SSN: 345-67-8901 and AKIAIOSFODNN7EXAMPLE"
        result = redactor.redact(text, "pii")
        assert "345-67-8901" not in result
        assert "AKIAIOSFODNN7EXAMPLE" not in result

    def test_secrets_tag_fires_all_rules(self, redactor: Redactor) -> None:
        """sensitivity_tag='secrets' fires ALL rules."""
        key = "AKIAIOSFODNN7EXAMPLE"
        result = redactor.redact(f"key={key}", "secrets")
        assert key not in result

    def test_unknown_tag_fires_all_rules(self, redactor: Redactor) -> None:
        """Unknown sensitivity tags fire all rules (conservative default)."""
        key = "AKIAIOSFODNN7EXAMPLE"
        result = redactor.redact(f"key={key}", "highly-classified")
        assert key not in result


# ---------------------------------------------------------------------------
# redact_for_handoff
# ---------------------------------------------------------------------------


class TestRedactForHandoff:
    def test_handoff_t1_to_t3_applies_max_redaction(self, redactor: Redactor) -> None:
        """from_tier=T1, to_tier=T3 → less trusted receiver → max redaction applied."""
        payload = {
            "summary": "Some fact with AWS key AKIAIOSFODNN7EXAMPLE",
            "content": "password=mysecret",
        }

        result = redactor.redact_for_handoff(payload, from_tier=Tier.T1, to_tier=Tier.T3)

        assert "AKIAIOSFODNN7EXAMPLE" not in str(result)
        assert "mysecret" not in str(result)

    def test_handoff_t1_to_t0_skips_extra_redaction(self, redactor: Redactor) -> None:
        """from_tier=T1, to_tier=T0 → more trusted receiver → no extra redaction."""
        payload = {
            "summary": "AWS key AKIAIOSFODNN7EXAMPLE",
        }

        result = redactor.redact_for_handoff(payload, from_tier=Tier.T1, to_tier=Tier.T0)

        # to_tier <= from_tier: T0 <= T1 (T0 is more trusted) → no extra redaction
        assert result is payload  # returns original unchanged

    def test_handoff_same_tier_no_extra_redaction(self, redactor: Redactor) -> None:
        """from_tier == to_tier → no extra redaction."""
        payload = {"data": "AKIAIOSFODNN7EXAMPLE"}
        result = redactor.redact_for_handoff(payload, from_tier=Tier.T1, to_tier=Tier.T1)
        assert result is payload

    def test_handoff_deep_dict_redaction(self, redactor: Redactor) -> None:
        """redact_for_handoff recursively redacts nested dicts and lists."""
        payload = {
            "outer": {
                "inner": "password: secret123",
            },
            "list_field": ["AKIAIOSFODNN7EXAMPLE", "innocent text"],
        }

        result = redactor.redact_for_handoff(payload, from_tier=Tier.T0, to_tier=Tier.T3)

        assert "secret123" not in str(result)
        assert "AKIAIOSFODNN7EXAMPLE" not in str(result)
        assert "innocent text" in str(result)

    def test_handoff_t2_to_t3_applies_redaction(self, redactor: Redactor) -> None:
        """T2 sender, T3 receiver → T3 > T2 → redaction applied."""
        payload = {"key": "AKIAIOSFODNN7EXAMPLE"}
        result = redactor.redact_for_handoff(payload, from_tier=Tier.T2, to_tier=Tier.T3)
        assert "AKIAIOSFODNN7EXAMPLE" not in str(result)


# ---------------------------------------------------------------------------
# REDACTION_PATTERNS module constant
# ---------------------------------------------------------------------------


class TestRedactionPatternsFrozen:
    def test_patterns_is_tuple(self) -> None:
        assert isinstance(REDACTION_PATTERNS, tuple)

    def test_patterns_has_expected_rules(self) -> None:
        rule_ids = {rule_id for rule_id, _ in REDACTION_PATTERNS}
        assert "SSN" in rule_ids
        assert "AWS_KEY" in rule_ids
        assert "GITHUB_TOKEN" in rule_ids
        assert "JWT" in rule_ids
        assert "PRIVATE_KEY" in rule_ids
        assert "PASSWORD" in rule_ids

    def test_patterns_count(self) -> None:
        """Exactly 6 redaction patterns at module level."""
        assert len(REDACTION_PATTERNS) == 6
