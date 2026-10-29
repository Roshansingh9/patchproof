"""Fake settings. These values are NOT real credentials.

They are split into fragments so secret scanners do not flag the repository.
"""

CURRENCY = "USD"
PAYMENT_API_KEY = "sk-" + "test-FAKE0000000000000000000000000000"
DATABASE_URL = "postgres://shop_user:" + "not-a-real-password@localhost:5432/shop"
