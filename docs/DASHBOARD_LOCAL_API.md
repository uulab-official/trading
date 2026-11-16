# Local dashboard API

The current implementation is described in [apps/dashboard/README.md](../apps/dashboard/README.md). The HTML **does** fetch `/api/snapshot`; the previous static-skeleton description is obsolete.

- The entire snapshot (summary, filtered page, recent audit) is read inside one read-only SQLite transaction.
- Health checks validate the real schema and rows, not mere file existence. Corrupt/unsupported data returns 503 with no private path or SQL details.
- URI filenames are encoded with `Path.as_uri()` before adding `mode=ro`; `?`, `#` and Unicode filenames cannot alter connection options.
- Connections are explicitly closed after every request. The service never creates/migrates a journal.
- No account payload, token or secret field is returned. Amounts stay decimal strings; currencies are never added together.
- Local Host / Origin checks are browser-origin safeguards, **not authentication**. Remote hosting remains unsupported.

No market-data freshness, portfolio equity or trading readiness is inferred from a successful journal read. API retrieval time and the last stored order timestamp are different signals.
