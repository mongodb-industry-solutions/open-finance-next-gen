# Open Finance Next Gen

Demonstrates how MongoDB Atlas powers secure Open Finance data exchange — consent management with Queryable Encryption.

> **This is one of three interconnected repositories that make up the Leafy Bank Open Finance solution:**
>
>
> | Repository                                                                                                                                           | Description                                                               | Port |
> | ------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------- | ------ |
> | **open-finance-next-gen** (this repo)                                                                                                                | FastAPI backend — consents, accounts, transactions, Queryable Encryption | 8003 |
> | [leafy-bank-backend-openfinance-reactagent-chatbot](https://github.com/mongodb-industry-solutions/leafy-bank-backend-openfinance-reactagent-chatbot) | LangGraph multi-agent chatbot — consent flows, financial advice          | 8080 |
> | [open-finance-next-gen-ui](https://github.com/mongodb-industry-solutions/open-finance-next-gen-ui)                                                   | Next.js 15 frontend — dashboard, multi-bank views, AI assistant          | 3000 |

## Where MongoDB Shines

- **Queryable Encryption for Consent Privacy**: Sensitive consent fields (consumer identity, permissions, source institution) are encrypted at rest and in transit. MongoDB's Queryable Encryption enables equality queries on encrypted fields without ever exposing plaintext to the server — critical for regulatory compliance in financial services.
- **Aggregation Pipelines for Financial Calculations**: MongoDB aggregation pipelines compute total balances, debt positions, and product comparisons across internal and external accounts in a single database operation.

## High-Level Architecture

![Architecture Diagram](architecture-diagram.png)

## Tech Stack

- **[MongoDB Atlas](https://www.mongodb.com/atlas)** for the dual-database data layer (external + internal bank data)
- **[MongoDB Queryable Encryption](https://www.mongodb.com/docs/manual/core/queryable-encryption/)** for encrypted consent storage with searchable fields
- **[FastAPI](https://fastapi.tiangolo.com/)** (Python) for the REST API backend
- **[SlowAPI](https://slowapi.readthedocs.io/)** for rate limiting (60 requests/minute)
- **[Poetry](https://python-poetry.org/)** for Python dependency management

## Prerequisites

Before you begin, ensure you have met the following requirements:

- **Python** 3.10 or higher (but less than 4.0)
- **Poetry** 1.8.4 (install via [Poetry's official docs](https://python-poetry.org/docs/#installation) or `pipx install poetry==1.8.4`)
- **MongoDB Atlas** cluster with Queryable Encryption support
- **Docker & Docker Compose** (optional, for containerized deployment)

## Initial Configuration

### Obtain Your MongoDB Connection String

1. Set up a [MongoDB Atlas](https://www.mongodb.com/atlas) cluster if you don't have one already.
2. Locate your cluster, click **Connect**, and select **Connect your application**.
3. Copy the connection string.

> You'll need this connection string for the `MONGODB_URI` environment variable.

### Clone the Repository

1. Open your terminal and navigate to the directory where you want to store the project:

   ```bash
   cd /path/to/your/desired/directory
   ```
2. Clone the repository:

   ```bash
   git clone <repository-url>
   ```
3. Navigate into the cloned project:

   ```bash
   cd open-finance-next-gen
   ```

### Set Up Queryable Encryption

Queryable Encryption requires a local master key and an encrypted consents collection with Data Encryption Keys (DEKs).

1. Generate a 96-byte local master key:

   ```bash
   python -c "import os; open('backend/master-key.bin', 'wb').write(os.urandom(96))"
   ```
2. Run the encrypted consents setup script:

   ```bash
   cd backend && poetry run python ../scripts/setup_encrypted_consents.py
   ```

   This creates the `openbankingConsents` collection in the `leafy_bank_bian` database, generates 4 DEKs, and saves `encryption_config.json` (gitignored).

> For production, replace the local master key with AWS KMS. Never commit `master-key.bin` or `encryption_config.json` to version control.

### Populate Seed Data

The demo requires seed data across two databases. Import the following collections into your Atlas cluster:

**Database: `leafy_bank_open_finance`**


| Collection              | Purpose                                   |
| ------------------------- | ------------------------------------------- |
| `tokens`                | Bearer token storage                      |
| `institutions`          | Available external banks                  |
| `external_accounts`     | Account data from external institutions   |
| `external_products`     | Loans/credit products from external banks |
| `external_transactions` | BIAN-aligned external transaction data    |

**Database: `leafy_bank_bian`**


| Collection            | Purpose                                                       |
| ----------------------- | --------------------------------------------------------------- |
| `accounts`            | Internal bank accounts                                        |
| `customers`           | Customer records (maps user identifiers to BIAN customerId)   |
| `transactions`        | BIAN-aligned internal transaction history                     |
| `openbankingConsents` | Queryable-Encrypted consent records (created by setup script) |
| `cachedExternalData`  | External-bank data cached after consent approval              |

> The `openbankingConsents` collection is created by `setup_encrypted_consents.py` — do not import it manually.

## Run it Locally

### Setup

1. (Optional) Set your project description and author information in `backend/pyproject.toml`:

   ```toml
   description = "Your Description"
   authors = [{name = "Your Name", email = "you@example.com"}]
   ```
2. Ensure you are in the root project directory where the `makefile` is located.
3. Run the setup commands:

   ```bash
   make setup
   ```

   This configures Poetry for in-project virtual environments and installs all dependencies. Verify that the `.venv` folder has been generated within the `backend/` directory.
4. Create a `backend/.env` file with your configuration:

   ```bash
   # MongoDB
   MONGODB_URI=
   OPENFINANCE_DB_NAME=leafy_bank_open_finance
   LEAFYBANK_DB_NAME=leafy_bank_bian
   ```

### Running Locally

Start the development server with:

```bash
make dev
```

- **API**: [http://localhost:8003](http://localhost:8003)
- **Swagger Docs**: [http://localhost:8003/docs](http://localhost:8003/docs)
- **ReDoc**: [http://localhost:8003/redoc](http://localhost:8003/redoc)

You can also run with different verbosity levels:

```bash
make run            # Production mode (no reload)
make run-verbose    # Debug logging
make logs           # Trace logging (most verbose)
```

**Quick health check:**

```bash
make check          # Verify app imports correctly
```

## Run with Docker

Make sure to run this from the root directory.

To run with Docker:

```bash
make build
```

The API will be available at [http://localhost:8080](http://localhost:8080).

To manage the container:

```bash
make start    # Start existing container
make stop     # Stop container
make clean    # Remove container and images
```

> **Note:** The Docker container runs on port 8080, while local development runs on port 8003.

## API Endpoints

### Authentication


| Method | Path                                           | Auth   | Purpose                     |
| -------- | ------------------------------------------------ | -------- | ----------------------------- |
| `GET`  | `/api/v1/openfinance/public/get-authorization` | None   | Get bearer token for a user |
| `POST` | `/api/v1/openfinance/public/create-user`       | None   | Create a new user and token |
| `POST` | `/api/v1/openfinance/secure/validate-token`    | Bearer | Validate token health       |

### Consent Management (Queryable Encrypted)


| Method   | Path                                                       | Purpose                                 |
| ---------- | ------------------------------------------------------------ | ----------------------------------------- |
| `POST`   | `/api/v1/openfinance/secure/consents`                      | Create consent (AWAITING_AUTHORISATION) |
| `GET`    | `/api/v1/openfinance/secure/consents`                      | List consents for a user                |
| `GET`    | `/api/v1/openfinance/secure/consents/{consent_id}`         | Retrieve consent by ID                  |
| `PATCH`  | `/api/v1/openfinance/secure/consents/{consent_id}/status`  | Update consent status (approve/reject)  |
| `POST`   | `/api/v1/openfinance/secure/consents/{consent_id}/approve` | Approve consent (explicit user action)  |
| `DELETE` | `/api/v1/openfinance/secure/consents/{consent_id}`         | Revoke consent                          |

### Consent-Gated Customer Data


| Method | Path                                                                | Purpose                                      |
| -------- | --------------------------------------------------------------------- | ---------------------------------------------- |
| `GET`  | `/api/v1/openfinance/secure/customers/{user}/external-data`         | All external data (requires`consent_id`)     |
| `GET`  | `/api/v1/openfinance/secure/customers/{user}/external-transactions` | External transactions (requires`consent_id`) |
| `POST` | `/api/v1/openfinance/secure/customers/{user}/fetch-and-cache`       | Fetch external data and cache it             |
| `GET`  | `/api/v1/openfinance/secure/customers/{user}/cached-data`           | Read previously cached external data         |
| `GET`  | `/api/v1/openfinance/secure/customers/{user}/global-position`       | Aggregated global financial position         |

### Institutions


| Method | Path                                                         | Purpose                       |
| -------- | -------------------------------------------------------------- | ------------------------------- |
| `GET`  | `/api/v1/openfinance/secure/institutions`                    | List available external banks |
| `GET`  | `/api/v1/openfinance/secure/institutions/{institution_name}` | Get a single institution      |

### External Data


| Method | Path                                                                          | Purpose                              |
| -------- | ------------------------------------------------------------------------------- | -------------------------------------- |
| `GET`  | `/api/v1/openfinance/secure/fetch-external-accounts-for-user-and-institution` | External accounts (institution)      |
| `GET`  | `/api/v1/openfinance/secure/fetch-external-products-for-user-and-institution` | External products (institution)      |
| `GET`  | `/api/v1/openfinance/secure/fetch-external-accounts-for-user`                 | External accounts (all consents)     |
| `GET`  | `/api/v1/openfinance/secure/fetch-external-products-for-user`                 | External products (all consents)     |
| `POST` | `/api/v1/openfinance/secure/calculate-total-balance-for-user`                 | Aggregate total balance              |
| `POST` | `/api/v1/openfinance/secure/calculate-total-debt-for-user`                    | Aggregate total debt                 |
| `POST` | `/api/v1/openfinance/secure/retrieve-external-account-for-user`               | Retrieve a specific external account |
| `POST` | `/api/v1/openfinance/secure/retrieve-external-product-for-user`               | Retrieve a specific external product |

### Leafy Bank Internal


| Method | Path                                                                       | Purpose                         |
| -------- | ---------------------------------------------------------------------------- | --------------------------------- |
| `POST` | `/api/v1/leafybank/accounts/secure/fetch-accounts-for-user`                | Internal accounts               |
| `POST` | `/api/v1/leafybank/users/secure/find-user`                                 | Resolve user → BIAN customerId |
| `POST` | `/api/v1/leafybank/transactions/secure/fetch-recent-transactions-for-user` | Recent internal transactions    |
| `GET`  | `/api/v1/leafybank/transactions/secure/spending/{user_identifier}`         | Spending transactions           |

### Encryption Demo


| Method | Path                                           | Purpose                               |
| -------- | ------------------------------------------------ | --------------------------------------- |
| `GET`  | `/api/v1/encryption-demo/compare/{consent_id}` | QE decrypted vs raw binary comparison |

## Core Capabilities

### Consent State Machine

Every consent follows a strict lifecycle:

```text
AWAITING_AUTHORISATION → AUTHORISED → CONSUMED
                       → REJECTED
AUTHORISED → REVOKED
```

- **Create** — Consent starts as `AWAITING_AUTHORISATION` with encrypted fields
- **Approve** — Transitions to `AUTHORISED` via `PATCH .../status` or the explicit `POST .../approve` endpoint (requires explicit user action)
- **Consume** — First data retrieval transitions to `CONSUMED` (one-time consents)
- **Reject/Revoke** — Terminal states that block further data access
- **Expiration** — Enforced at application level (TTL indexes incompatible with QE)

### Queryable Encryption

Four consent fields are encrypted using MongoDB's Queryable Encryption:


| Field                               | Query Type            | Purpose                      |
| ------------------------------------- | ----------------------- | ------------------------------ |
| `Consumer.UserName`                 | Equality              | Look up consents by user     |
| `Consumer.UserId`                   | Equality              | Look up consents by ID       |
| `Permissions`                       | None (encrypted only) | Protect permission details   |
| `SourceInstitution.InstitutionName` | None (encrypted only) | Protect institution identity |

The driver encrypts on write and decrypts on read — the Atlas server never sees plaintext. The encrypted `openbankingConsents` collection lives in the `leafy_bank_bian` database. A local 96-byte master key protects the Data Encryption Keys (swap to AWS KMS for production).

## Common Errors

### Backend Errors

- **`Command not found: uvicorn`** — Run `make setup` to install dependencies.
- **`Address already in use`** — Kill the existing process: `lsof -ti :8003 | xargs kill -9`.
- **MongoDB connection failures** — Check that your `MONGODB_URI` is correct and your Atlas cluster is accessible.
- **Queryable Encryption errors** — Ensure `master-key.bin` exists and `encryption_config.json` was generated by the setup script.

### Consent Errors

- **403 on data retrieval** — The consent may be expired, consumed, or revoked. Check consent status first.
- **Encryption setup fails** — The `openbankingConsents` collection may already exist. Drop it and re-run `setup_encrypted_consents.py`.
- **TTL index error** — TTL indexes are not supported on QE-encrypted collections. Expiration is enforced in application code.

## External Consumers

This API serves as the backend for:

- **[Agentic Chatbot](../leafy-bank-backend-openfinance-react-agent-chatbot/)** — Multi-agent LangGraph chatbot that calls this API for consent management, institution lookups, and external bank data retrieval. Runs on port 8080, connects to this service on port 8003.

## Additional Resources

### MongoDB Resources

- [MongoDB for Financial Services](https://www.mongodb.com/solutions/industries/financial-services)
- [MongoDB Atlas](https://www.mongodb.com/atlas)
- [MongoDB Queryable Encryption](https://www.mongodb.com/docs/manual/core/queryable-encryption/)
- [MongoDB Aggregation Pipelines](https://www.mongodb.com/docs/manual/aggregation/)

### Frameworks and Services

- [FastAPI](https://fastapi.tiangolo.com/) — Python async API framework
- [SlowAPI](https://slowapi.readthedocs.io/) — Rate limiting for FastAPI
- [Poetry](https://python-poetry.org/) — Python dependency management
