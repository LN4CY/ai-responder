# Release Notes - v1.5.0

## 🚀 Overview
Version 1.5.0 is a critical reliability release that addresses edge cases in message acknowledgment and improves the project's CI/CD pipeline efficiency.

## 🛠 Fixes & Improvements

### 🔗 Mesh Delivery & Acknowledgment Robustness
- **Implicit ACK Filtering (`fix/immediate-ack`)**: Fixed a bug where the handler would misinterpret local implicit ACKs (e.g., messages with `sender=0` or self-echos) as successful deliveries to remote nodes. This ensures the AI Responder correctly tracks when a message actually reaches its destination, deeply improving reliability over slow or congested links.

### ⚙️ CI/CD & Pipeline Enhancements
- **Container Retention Policy (`chore(ci)`)**: Updated the GitHub Actions container retention policy to `v3.0`. This also includes fixes to the policy's account type and permissions logic, ensuring older Docker images are properly pruned from the GitHub Container Registry, maintaining a clean releases page.

## 📦 Upgrade Instructions
1.  Pull the latest Docker image: `docker pull ghcr.io/ln4cy/ai-responder:latest`
2.  Restart the container: `docker-compose up -d`
