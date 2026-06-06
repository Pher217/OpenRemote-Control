import time

from apps.hostlink.security import sign, verify_sig

SECRET = "test-secret-key"
HOST_ID = "abc-123"
NONCE = "unique-nonce-1"


def _now_ts() -> str:
    return str(int(time.time()))


class TestSign:
    def test_returns_64_char_hex(self):
        """
        GIVEN valid inputs
        WHEN sign() is called
        THEN it returns a 64-character hex string (sha256)
        """
        result = sign(SECRET, HOST_ID, _now_ts(), NONCE)
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_deterministic(self):
        """
        GIVEN the same inputs
        WHEN sign() is called twice
        THEN it returns the same value
        """
        ts = _now_ts()
        assert sign(SECRET, HOST_ID, ts, NONCE) == sign(SECRET, HOST_ID, ts, NONCE)

    def test_different_secret_differs(self):
        """
        GIVEN two different secrets
        WHEN sign() is called with each
        THEN results differ
        """
        ts = _now_ts()
        assert sign("s1", HOST_ID, ts, NONCE) != sign("s2", HOST_ID, ts, NONCE)


class TestVerifySig:
    def test_valid_signature_accepted(self):
        """
        GIVEN a fresh timestamp and valid signature
        WHEN verify_sig() is called
        THEN it returns (True, "")
        """
        ts = _now_ts()
        sig = sign(SECRET, HOST_ID, ts, NONCE)
        ok, reason = verify_sig(
            SECRET, HOST_ID, ts, NONCE, sig, now=float(ts)
        )
        assert ok is True
        assert reason == ""

    def test_expired_timestamp_rejected(self):
        """
        GIVEN a timestamp 301 seconds in the past
        WHEN verify_sig() is called
        THEN it returns (False, "ts_expired")
        """
        ts = str(int(time.time()) - 301)
        sig = sign(SECRET, HOST_ID, ts, NONCE)
        ok, reason = verify_sig(
            SECRET, HOST_ID, ts, NONCE, sig, now=time.time()
        )
        assert ok is False
        assert reason == "ts_expired"

    def test_tampered_signature_rejected(self):
        """
        GIVEN a valid timestamp but a tampered signature
        WHEN verify_sig() is called
        THEN it returns (False, "bad_signature")
        """
        ts = _now_ts()
        ok, reason = verify_sig(
            SECRET, HOST_ID, ts, NONCE, "deadbeef" * 8, now=float(ts)
        )
        assert ok is False
        assert reason == "bad_signature"

    def test_non_integer_ts_rejected(self):
        """
        GIVEN a non-integer timestamp string
        WHEN verify_sig() is called
        THEN it returns (False, "invalid_ts")
        """
        ok, reason = verify_sig(
            SECRET, HOST_ID, "not-a-number", NONCE, "sig", now=time.time()
        )
        assert ok is False
        assert reason == "invalid_ts"

    def test_future_timestamp_within_skew_accepted(self):
        """
        GIVEN a timestamp 299 seconds in the future (within default skew)
        WHEN verify_sig() is called
        THEN it returns (True, "")
        """
        ts = str(int(time.time()) + 299)
        sig = sign(SECRET, HOST_ID, ts, NONCE)
        ok, reason = verify_sig(
            SECRET, HOST_ID, ts, NONCE, sig, now=time.time()
        )
        assert ok is True
        assert reason == ""
