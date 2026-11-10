# Dhan fixtures

Two kinds of file live here, and they are not equally trustworthy:

1. **`error_808.json`** is a real response. It was captured from
   `POST https://api.dhan.co/v2/optionchain/expirylist` on 9 November 2026 with
   deliberately invalid credentials, which is the one call this pack can make
   without an account. It is why the adapter understands Dhan's
   `{"data": {"808": "..."}, "status": "failed"}` envelope as well as the
   `{errorCode, errorMessage}` one.
2. **Everything else** is built to Dhan's documented v2 schema, filled with this
   pack's 9 November 2026 session numbers (NIFTY 25,044.65, the 24850 PE at
   Rs 246.10, instrument ids in the same range as the real scrip master). No
   authenticated call was made, because that needs a paid Data API subscription
   and a token.

When a real token is available, four calls are worth capturing to replace the
synthetic files with true ones: `/optionchain`, `/optionchain/expirylist`,
`/fundlimit`, and one `POST /orders` plus its `GET /orders/{id}`. Nothing else in
the code depends on the file names - the tests route by endpoint.
