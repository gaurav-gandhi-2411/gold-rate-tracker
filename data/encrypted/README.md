# data/encrypted/

Raw third-party data this public repository must not republish is committed here only as
ciphertext (ADR 060, `docs/adr/060-encrypted-raw-data.md`). Examples: IBJA's rate history,
retailer rates, and the Yahoo Finance snapshots behind registered research results.

For a logical path `P`, such as `data/ibja_rates.parquet`:

| File | What it is |
|---|---|
| `data/encrypted/P.enc` | AES-256-GCM ciphertext. The header binds the path and schema version. |
| `data/encrypted/P.sha256.json` | Public manifest entry: the plaintext SHA-256 and size, the ciphertext SHA-256 and the key id. |

**Checking a decrypted copy.** No key is needed. Put the file at its logical path, then run
`python scripts/data_crypt.py verify P --hash-only`. It checks the file against its SHA-256 in
the manifest entry.

**Decrypting.** You need the `DATA_ENC_KEY` secret. Set it in your environment (never pass it
as an argument), then run:

```
python scripts/data_crypt.py decrypt --all
```

The plaintext files are gitignored.

**Do not edit anything in this folder by hand.** CI writes it (`data_crypt.py encrypt`), and
the lint job's `data_crypt.py guard` checks it.
