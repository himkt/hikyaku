"""Tests for Auth0 integration — JWT validation and user identity.

Covers: Auth0 settings in config.py, Auth0Verifier class in auth.py,
verify_auth0_user dependency, and get_user_id helper.

These tests verify the design doc specification for Step 1:
- auth0_domain and auth0_client_id settings loaded from env vars
- Auth0Verifier.get_jwks_client() singleton with correct JWKS URL
- verify_auth0_user: JWT validation, scope storage, error handling
- get_user_id: sub claim extraction from request scope
"""

import os
from unittest.mock import MagicMock, patch

import jwt as pyjwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from hikyaku_registry.auth import Auth0Verifier, verify_auth0_user, get_user_id


# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------

_TEST_AUTH0_DOMAIN = "test-tenant.auth0.com"
_TEST_AUTH0_CLIENT_ID = "test-client-id-123"
_TEST_JWT_TOKEN = "eyJhbGciOiJSUzI1NiJ9.test-payload.test-signature"
_TEST_SUB = "auth0|user123"
_TEST_DECODED_TOKEN = {
    "sub": _TEST_SUB,
    "aud": _TEST_AUTH0_CLIENT_ID,
    "iss": f"https://{_TEST_AUTH0_DOMAIN}/",
    "exp": 9999999999,
    "iat": 1000000000,
}


# ===========================================================================
# Auth0 Settings tests
# ===========================================================================


class TestAuth0Settings:
    """Tests for Auth0 configuration in Settings class.

    auth0_domain and auth0_client_id settings must exist and load
    from AUTH0_DOMAIN and AUTH0_CLIENT_ID environment variables.
    """

    def test_auth0_domain_field_exists(self):
        """Settings class has auth0_domain field."""
        from hikyaku_registry.config import Settings

        assert "auth0_domain" in Settings.model_fields

    def test_auth0_client_id_field_exists(self):
        """Settings class has auth0_client_id field."""
        from hikyaku_registry.config import Settings

        assert "auth0_client_id" in Settings.model_fields

    def test_auth0_domain_loaded_from_env(self):
        """auth0_domain is loaded from AUTH0_DOMAIN env var."""
        from hikyaku_registry.config import Settings

        env = {
            "AUTH0_DOMAIN": "custom.auth0.com",
            "AUTH0_CLIENT_ID": "placeholder",
        }
        with patch.dict(os.environ, env, clear=False):
            s = Settings()
            assert s.auth0_domain == "custom.auth0.com"

    def test_auth0_client_id_loaded_from_env(self):
        """auth0_client_id is loaded from AUTH0_CLIENT_ID env var."""
        from hikyaku_registry.config import Settings

        env = {
            "AUTH0_DOMAIN": "placeholder.auth0.com",
            "AUTH0_CLIENT_ID": "my-client-id",
        }
        with patch.dict(os.environ, env, clear=False):
            s = Settings()
            assert s.auth0_client_id == "my-client-id"


# ===========================================================================
# Auth0Verifier tests
# ===========================================================================


class TestAuth0Verifier:
    """Tests for Auth0Verifier class with cached PyJWKClient.

    The class provides a singleton PyJWKClient via get_jwks_client(),
    using settings.auth0_domain to construct the JWKS URL.
    """

    @pytest.fixture(autouse=True)
    def reset_jwks_cache(self):
        """Reset class-level JWKS client cache between tests."""
        Auth0Verifier._jwks_client = None
        yield
        Auth0Verifier._jwks_client = None

    @patch("hikyaku_registry.auth.settings")
    @patch("hikyaku_registry.auth.jwt.PyJWKClient")
    def test_returns_pyjwkclient_instance(self, mock_pyjwk_cls, mock_settings):
        """get_jwks_client() returns a PyJWKClient instance."""
        mock_settings.auth0_domain = _TEST_AUTH0_DOMAIN
        mock_instance = MagicMock()
        mock_pyjwk_cls.return_value = mock_instance

        result = Auth0Verifier.get_jwks_client()

        assert result is mock_instance

    @patch("hikyaku_registry.auth.settings")
    @patch("hikyaku_registry.auth.jwt.PyJWKClient")
    def test_caches_client_across_calls(self, mock_pyjwk_cls, mock_settings):
        """Second call returns same instance (class-level singleton)."""
        mock_settings.auth0_domain = _TEST_AUTH0_DOMAIN

        first = Auth0Verifier.get_jwks_client()
        second = Auth0Verifier.get_jwks_client()

        assert first is second
        mock_pyjwk_cls.assert_called_once()

    @patch("hikyaku_registry.auth.settings")
    @patch("hikyaku_registry.auth.jwt.PyJWKClient")
    def test_jwks_url_constructed_from_auth0_domain(
        self, mock_pyjwk_cls, mock_settings
    ):
        """JWKS URL is https://{auth0_domain}/.well-known/jwks.json."""
        mock_settings.auth0_domain = _TEST_AUTH0_DOMAIN

        Auth0Verifier.get_jwks_client()

        expected_url = f"https://{_TEST_AUTH0_DOMAIN}/.well-known/jwks.json"
        mock_pyjwk_cls.assert_called_once_with(
            expected_url, cache_keys=True, lifespan=60 * 60 * 24
        )


# ===========================================================================
# verify_auth0_user tests
# ===========================================================================


class TestVerifyAuth0User:
    """Tests for verify_auth0_user FastAPI dependency.

    Validates Auth0 JWT via PyJWKClient + jwt.decode, stores decoded
    token in request.scope['auth0'] and raw token in request.scope['token'].
    Raises 401 with WWW-Authenticate header on any InvalidTokenError.
    """

    @pytest.fixture(autouse=True)
    def reset_jwks_cache(self):
        """Reset class-level JWKS client cache between tests."""
        Auth0Verifier._jwks_client = None
        yield
        Auth0Verifier._jwks_client = None

    def _make_request(self) -> MagicMock:
        """Create a mock Request with a mutable scope dict."""
        request = MagicMock()
        request.scope = {}
        return request

    def _make_cred(self, token: str = _TEST_JWT_TOKEN) -> HTTPAuthorizationCredentials:
        """Create HTTPAuthorizationCredentials."""
        return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    # --- Valid JWT ---

    @pytest.mark.asyncio
    @patch("hikyaku_registry.auth.settings")
    @patch("hikyaku_registry.auth.jwt.decode")
    @patch.object(Auth0Verifier, "get_jwks_client")
    async def test_valid_jwt_stores_decoded_token_in_scope(
        self, mock_get_jwks, mock_decode, mock_settings
    ):
        """Valid JWT: decoded token stored in request.scope['auth0']."""
        mock_settings.auth0_client_id = _TEST_AUTH0_CLIENT_ID
        mock_key = MagicMock()
        mock_get_jwks.return_value.get_signing_key_from_jwt.return_value = mock_key
        mock_decode.return_value = _TEST_DECODED_TOKEN

        request = self._make_request()
        await verify_auth0_user(request, self._make_cred())

        assert request.scope["auth0"] == _TEST_DECODED_TOKEN

    @pytest.mark.asyncio
    @patch("hikyaku_registry.auth.settings")
    @patch("hikyaku_registry.auth.jwt.decode")
    @patch.object(Auth0Verifier, "get_jwks_client")
    async def test_valid_jwt_stores_raw_token_in_scope(
        self, mock_get_jwks, mock_decode, mock_settings
    ):
        """Valid JWT: raw token string stored in request.scope['token']."""
        mock_settings.auth0_client_id = _TEST_AUTH0_CLIENT_ID
        mock_key = MagicMock()
        mock_get_jwks.return_value.get_signing_key_from_jwt.return_value = mock_key
        mock_decode.return_value = _TEST_DECODED_TOKEN

        request = self._make_request()
        await verify_auth0_user(request, self._make_cred())

        assert request.scope["token"] == _TEST_JWT_TOKEN

    @pytest.mark.asyncio
    @patch("hikyaku_registry.auth.settings")
    @patch("hikyaku_registry.auth.jwt.decode")
    @patch.object(Auth0Verifier, "get_jwks_client")
    async def test_decodes_with_rs256_and_correct_audience(
        self, mock_get_jwks, mock_decode, mock_settings
    ):
        """JWT decoded with RS256 algorithm and auth0_client_id as audience."""
        mock_settings.auth0_client_id = _TEST_AUTH0_CLIENT_ID
        mock_key = MagicMock()
        mock_get_jwks.return_value.get_signing_key_from_jwt.return_value = mock_key
        mock_decode.return_value = _TEST_DECODED_TOKEN

        await verify_auth0_user(self._make_request(), self._make_cred())

        mock_decode.assert_called_once_with(
            jwt=_TEST_JWT_TOKEN,
            key=mock_key.key,
            algorithms=["RS256"],
            audience=_TEST_AUTH0_CLIENT_ID,
        )

    @pytest.mark.asyncio
    @patch("hikyaku_registry.auth.settings")
    @patch("hikyaku_registry.auth.jwt.decode")
    @patch.object(Auth0Verifier, "get_jwks_client")
    async def test_extracts_signing_key_from_jwt_token(
        self, mock_get_jwks, mock_decode, mock_settings
    ):
        """Signing key is extracted from JWT via get_signing_key_from_jwt."""
        mock_settings.auth0_client_id = _TEST_AUTH0_CLIENT_ID
        mock_jwks = MagicMock()
        mock_get_jwks.return_value = mock_jwks
        mock_decode.return_value = _TEST_DECODED_TOKEN

        await verify_auth0_user(self._make_request(), self._make_cred())

        mock_jwks.get_signing_key_from_jwt.assert_called_once_with(_TEST_JWT_TOKEN)

    # --- Invalid JWT (error cases) ---

    @pytest.mark.asyncio
    @patch("hikyaku_registry.auth.jwt.decode")
    @patch.object(Auth0Verifier, "get_jwks_client")
    async def test_expired_jwt_raises_401(self, mock_get_jwks, mock_decode):
        """Expired JWT raises HTTPException with status 401."""
        mock_key = MagicMock()
        mock_get_jwks.return_value.get_signing_key_from_jwt.return_value = mock_key
        mock_decode.side_effect = pyjwt.exceptions.ExpiredSignatureError()

        with pytest.raises(HTTPException) as exc_info:
            await verify_auth0_user(self._make_request(), self._make_cred())

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    @patch("hikyaku_registry.auth.jwt.decode")
    @patch.object(Auth0Verifier, "get_jwks_client")
    async def test_wrong_audience_raises_401(self, mock_get_jwks, mock_decode):
        """JWT with wrong audience raises HTTPException with status 401."""
        mock_key = MagicMock()
        mock_get_jwks.return_value.get_signing_key_from_jwt.return_value = mock_key
        mock_decode.side_effect = pyjwt.exceptions.InvalidAudienceError()

        with pytest.raises(HTTPException) as exc_info:
            await verify_auth0_user(self._make_request(), self._make_cred())

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    @patch("hikyaku_registry.auth.jwt.decode")
    @patch.object(Auth0Verifier, "get_jwks_client")
    async def test_invalid_signature_raises_401(self, mock_get_jwks, mock_decode):
        """JWT with invalid signature raises HTTPException with status 401."""
        mock_key = MagicMock()
        mock_get_jwks.return_value.get_signing_key_from_jwt.return_value = mock_key
        mock_decode.side_effect = pyjwt.exceptions.InvalidSignatureError()

        with pytest.raises(HTTPException) as exc_info:
            await verify_auth0_user(self._make_request(), self._make_cred())

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    @patch("hikyaku_registry.auth.jwt.decode")
    @patch.object(Auth0Verifier, "get_jwks_client")
    async def test_generic_invalid_token_raises_401(self, mock_get_jwks, mock_decode):
        """Any InvalidTokenError subclass raises HTTPException 401."""
        mock_key = MagicMock()
        mock_get_jwks.return_value.get_signing_key_from_jwt.return_value = mock_key
        mock_decode.side_effect = pyjwt.exceptions.InvalidTokenError()

        with pytest.raises(HTTPException) as exc_info:
            await verify_auth0_user(self._make_request(), self._make_cred())

        assert exc_info.value.status_code == 401

    # --- 401 response details ---

    @pytest.mark.asyncio
    @patch("hikyaku_registry.auth.jwt.decode")
    @patch.object(Auth0Verifier, "get_jwks_client")
    async def test_401_includes_www_authenticate_header(
        self, mock_get_jwks, mock_decode
    ):
        """401 response includes WWW-Authenticate: Bearer header."""
        mock_key = MagicMock()
        mock_get_jwks.return_value.get_signing_key_from_jwt.return_value = mock_key
        mock_decode.side_effect = pyjwt.exceptions.InvalidTokenError()

        with pytest.raises(HTTPException) as exc_info:
            await verify_auth0_user(self._make_request(), self._make_cred())

        assert exc_info.value.headers == {"WWW-Authenticate": "Bearer"}

    @pytest.mark.asyncio
    @patch("hikyaku_registry.auth.jwt.decode")
    @patch.object(Auth0Verifier, "get_jwks_client")
    async def test_401_has_descriptive_detail(self, mock_get_jwks, mock_decode):
        """401 response has 'Invalid authentication credentials' detail."""
        mock_key = MagicMock()
        mock_get_jwks.return_value.get_signing_key_from_jwt.return_value = mock_key
        mock_decode.side_effect = pyjwt.exceptions.InvalidTokenError()

        with pytest.raises(HTTPException) as exc_info:
            await verify_auth0_user(self._make_request(), self._make_cred())

        assert exc_info.value.detail == "Invalid authentication credentials"


# ===========================================================================
# get_user_id tests
# ===========================================================================


class TestGetUserId:
    """Tests for get_user_id helper.

    Extracts Auth0 sub claim from request.scope['auth0']['sub'].
    Returns 401 if scope is missing or has no sub.
    """

    def _make_request(self, scope: dict | None = None) -> MagicMock:
        """Create a mock Request with the given scope."""
        request = MagicMock()
        request.scope = scope if scope is not None else {}
        return request

    def test_returns_sub_claim(self):
        """Returns the sub claim from decoded Auth0 token in scope."""
        request = self._make_request(
            scope={
                "auth0": {"sub": _TEST_SUB},
            }
        )

        assert get_user_id(request) == _TEST_SUB

    def test_missing_auth0_scope_raises_401(self):
        """Missing request.scope['auth0'] raises HTTPException 401."""
        request = self._make_request(scope={})

        with pytest.raises(HTTPException) as exc_info:
            get_user_id(request)

        assert exc_info.value.status_code == 401

    def test_auth0_without_sub_raises_401(self):
        """request.scope['auth0'] without 'sub' key raises HTTPException 401."""
        request = self._make_request(
            scope={
                "auth0": {"aud": "some-audience"},
            }
        )

        with pytest.raises(HTTPException) as exc_info:
            get_user_id(request)

        assert exc_info.value.status_code == 401

    def test_sub_is_none_raises_401(self):
        """request.scope['auth0']['sub'] = None raises HTTPException 401."""
        request = self._make_request(
            scope={
                "auth0": {"sub": None},
            }
        )

        with pytest.raises(HTTPException) as exc_info:
            get_user_id(request)

        assert exc_info.value.status_code == 401

    def test_various_sub_formats(self):
        """Correctly returns various Auth0 sub claim formats."""
        subs = [
            "auth0|abc123",
            "google-oauth2|456789",
            "email|user@example.com",
        ]
        for sub in subs:
            request = self._make_request(scope={"auth0": {"sub": sub}})
            assert get_user_id(request) == sub
