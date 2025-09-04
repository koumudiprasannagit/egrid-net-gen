# eGRID Net Generation Viewer

A demo project to **ingest, store, and visualize the annual net generation of U.S. power plants**.  
It can run locally using Docker Compose .

---

## 🚀 Features

- **CSV ingestion** from an S3-compatible bucket (MinIO locally)
- **Transformation & storage** into DynamoDB
- **REST API** (FastAPI) to:
  - Retrieve **Top N power plants** by net generation
  - Filter by **U.S. state**
  - Search by plant name
- **Web UI** to select a state, choose Top N, and view results in table or chart
- **Containerized** with Docker + docker-compose
- **Monitoring** via  `docker compose logs` (local)

---

## 🛠️ Local Setup (Docker Compose)

### Prerequisites
- [Docker Desktop](https://www.docker.com/products/docker-desktop) (with WSL2 backend)
- Git (to clone this repo)

### Steps
```bash
# Clone this repo
git clone https://github.com/koumudiprasannagit/egrid-net-gen.git
cd egrid-net-gen

# Build and start
docker compose up -d --build

Services:
Frontend UI → http://localhost:8080
API (FastAPI) → http://localhost:8001
MinIO Console → http://localhost:9001
 (login: minioadmin/minioadmin)
DynamoDB Local → http://localhost:8000

Upload your CSV file into the MinIO bucket egrid/incoming/.
The ingest service will process it, write to DynamoDB, and move it to processed/.


Monitoring (Local):
docker compose logs -f ingest
or view logs in Docker Desktop.

Tech Choices:
FastAPI → modern, async-friendly REST framework
DynamoDB → scalable, serverless NoSQL store
MinIO → local S3-compatible storage
Docker Compose → simple local orchestration
