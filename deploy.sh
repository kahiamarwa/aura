#!/bin/bash
set -e

echo "=== Deploying Aura ==="

cd "$(dirname "$0")"

# Pull latest code
echo ">>> Pulling latest code..."
git pull origin frontend

# Build and restart containers
echo ">>> Building Docker images..."
docker compose build --no-cache

echo ">>> Restarting services..."
docker compose up -d

# Wait for backend health
echo ">>> Waiting for backend to be healthy..."
for i in {1..30}; do
    if docker inspect --format='{{.State.Health.Status}}' aura-backend 2>/dev/null | grep -q healthy; then
        echo "Backend is healthy!"
        break
    fi
    echo "  Waiting... ($i/30)"
    sleep 3
done

# Show status
echo ""
echo "=== Service Status ==="
docker compose ps
echo ""
echo "=== Backend Logs (last 20 lines) ==="
docker compose logs --tail=20 backend
echo ""
echo "=== Deploy complete ==="
