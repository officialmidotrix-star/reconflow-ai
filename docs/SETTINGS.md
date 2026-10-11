# Settings (environment variables) — the whole list

Set them on Render: web service → **Environment**. "Required" = the app will not start properly without it.

| Name | What it does | Required? |
|---|---|---|
| `RECONFLOW_DATABASE_URL` | The database. Use the **Internal Database URL** of your Render Postgres. | **Yes** |
| `RECONFLOW_ENCRYPTION_KEY` | Key that encrypts every stored file. **Never change it** — files made with an old key become unreadable. | **Yes** |
| `RECONFLOW_AUTO_MIGRATE` | `1` = the app brings its own database up to date when it starts. Keep it `1`. | Yes |
| `RECONFLOW_OPERATOR_EMAIL` | Your admin login (`official.midotrix@gmail.com`). | Yes |
| `RECONFLOW_SEED_OPERATOR_PASSWORD` | Password for that account **only if it does not exist yet**. Delete it after the first start. | First start only |
| `RECONFLOW_ALLOWED_ORIGINS` | Addresses of the frontend that may call the API, comma-separated. Needed once the frontend leaves your own computer. | When the frontend is online |
| `RECONFLOW_SEED_DEMO_DATA` | `1` = create the clearly labelled **TEST DATA** organization (3 branches, test commission rates 20% / 15% / 30%). Remove before real clients. | Optional (test deploy) |
| `RECONFLOW_STORAGE_BACKEND` | `local` (default: files on the server's disk — **lost on redeploy on Render**) or `s3` (an S3-compatible bucket — survives). | Optional |
| `RECONFLOW_S3_BUCKET` | Bucket name (only for `s3`). | With `s3` |
| `RECONFLOW_S3_ENDPOINT_URL` | The provider's address (Cloudflare R2, Backblaze B2, MinIO…). Empty for AWS S3. | With some providers |
| `RECONFLOW_S3_REGION` | Region (only if the provider asks). | Optional |
| `RECONFLOW_S3_PREFIX` | A folder inside the bucket, e.g. `reconflow/`. | Optional |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | The bucket's access key pair (standard names, used for any S3-compatible provider). | With `s3` |
| `RECONFLOW_STORAGE_DIR` | Folder for `local` storage. | Optional |
| `RECONFLOW_DB_POOL_SIZE` / `RECONFLOW_DB_MAX_OVERFLOW` | Database connections kept / allowed extra. Defaults 10 / 10. | Optional |
| `RECONFLOW_THREAD_LIMIT` | How many requests run at the same time. Default 16 (keep it below pool size + overflow). | Optional |
| `ANTHROPIC_API_KEY` | Turns on the real AI summary text. Without it a plain template is used. | Optional |
| `SMTP_HOST`, `SMTP_FROM_ADDRESS` (+ login settings) | Real e-mail notifications. Without them notifications are only recorded. | Optional |

Old names no longer used: `RECONFLOW_SEED_OWNER_EMAIL`, `RECONFLOW_SEED_OWNER_PASSWORD` (delete them).
