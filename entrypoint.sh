#!/bin/bash

echo ""
echo "=========================================="
echo "  Music Grabber is starting..."
echo "  Access at: http://localhost:38274"
echo "=========================================="
echo ""

exec uvicorn app:app --host 0.0.0.0 --port 8080
