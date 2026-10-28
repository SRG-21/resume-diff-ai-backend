# Resume Diff AI Backend

FastAPI backend server for Resume Diff AI application.

## Setup

### Prerequisites
- Python 3.8 or higher
- pip

### Installation

1. Create a virtual environment:
```bash
python -m venv venv
```

2. Activate the virtual environment:
- Windows:
  ```bash
  .\venv\Scripts\activate
  ```
- Linux/Mac:
  ```bash
  source venv/bin/activate
  ```

3. Install dependencies:
```bash
pip install -r requirements-dev.txt   # includes test tools; use requirements.txt for production
```

4. Create a `.env` file based on `.env.example`:
```bash
cp .env.example .env
```

### Running the Server

Start the development server:
```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

The API will be available at:
- API: http://localhost:8000
- Interactive API docs (Swagger): http://localhost:8000/docs
- Alternative API docs (ReDoc): http://localhost:8000/redoc

## API Endpoints

| Area | Endpoints | Plan |
|---|---|---|
| Service | `GET /`, `GET /health` | public |
| Auth | `POST /api/auth/signup`, `login`, `logout`, `refresh`, `google`, `verify-email`, `resend-verification`, `forgot-password`, `reset-password`, `GET /me`, `GET /validate`, `DELETE /account` | public / user |
| Compare | `POST /api/compare` (resume file or saved `resume_id`; JD as file, text or `jd_url`) | free: 3 scans/mo |
| History | `GET /api/history`, `GET/DELETE /api/history/{id}`, `DELETE /api/history` | free: latest 5 |
| Saved resumes | `GET/POST /api/resumes`, `DELETE /api/resumes/{id}` | free: 1, paid: 10+ |
| Job import | `POST /api/import-job` | paid |
| AI tools | `POST /api/ai/rewrites`, `/api/ai/cover-letter`, `/api/ai/interview-prep`, `GET /api/ai/{id}` | paid |
| Export | `POST /api/export/tailored` (DOCX) | paid |
| Tracker | `GET/POST /api/applications`, `PATCH/DELETE /api/applications/{id}` | paid |
| Bulk ranking | `POST /api/bulk/rank` | coach |
| Billing | `GET /api/billing/status`, `POST /api/billing/checkout`, `/portal`, `/webhook` | user / Stripe |

Interactive docs are at `/docs`.

## Plans and quotas

Plans live in `plans.py`. A user's *effective* plan is computed from the subscription fields on the user document, which only the Stripe webhook writes, so a cancelled subscription or an expired Job Hunt Pass falls back to Free with no cron job.

| | Free | Pro / Job Hunt Pass | Coach |
|---|---|---|---|
| Scans per month | 3 | 300 | 1500 |
| AI actions per month (rewrites, cover letters, interview prep) | 0 | 200 | 800 |
| Saved resumes | 1 | 10 | 50 |
| Breakdown, full ATS report, tracker, URL import, export | no | yes | yes |
| Bulk ranking | no | no | up to 10 resumes per run |

Quota is reserved atomically before an OpenAI call and refunded if the AI service fails, so users are never charged a scan for an error.

## Stripe setup

1. In the Stripe dashboard create Prices: Pro monthly, Pro yearly (recurring), Job Hunt Pass (one-time) and Coach monthly (recurring).
2. Put the price ids and `STRIPE_SECRET_KEY` in `.env` (or the AWS secret `resume-diff-ai/secrets`).
3. Add a webhook endpoint `https://<api>/api/billing/webhook` with events `checkout.session.completed`, `customer.subscription.created`, `customer.subscription.updated`, `customer.subscription.deleted`, `invoice.payment_failed`, and set `STRIPE_WEBHOOK_SECRET`.
4. Enable the Customer Portal in Stripe settings (cancel, update card, switch plan).
5. Locally: `stripe listen --forward-to localhost:8000/api/billing/webhook`.

## Deployment notes

- `JWT_SECRET_KEY` is **required** when `ENVIRONMENT=production`; the app refuses to start without it. Add it (and `MONGODB_URI`, `OPENAI_API_KEY`, the Stripe and Google keys) to the Secrets Manager secret.
- API Gateway HTTP APIs time out at 29 seconds even though the Lambda allows 60. The OpenAI timeout defaults to 25 seconds; keep `OPENAI_TIMEOUT` below 29.
- Saved comparisons expire after `HISTORY_RETENTION_DAYS` (MongoDB TTL index). Users can delete any record or their whole account (`DELETE /api/auth/account`), which also cancels active subscriptions.
- Job URL import blocks private and loopback addresses and re-checks every redirect, but it resolves DNS before connecting. If you run this outside Lambda, also restrict egress at the network layer.

## Testing

```bash
pip install -r requirements-dev.txt
python -m pytest
```

Tests use an in-memory MongoDB (`mongomock-motor`) and mock OpenAI and Stripe; no network or credentials needed.
