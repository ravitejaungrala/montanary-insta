# GTM LinkedIn — hosted "cloud login" browser (Phase 2).
# A Fargate task the user drives via noVNC to log into LinkedIn; on success the
# full browser profile is captured to S3 (profile_store) and the account activated.
#
# Built from the apps/backend CONTEXT (same as Dockerfile.worker) and pushed to
# ECR automatically by .github/workflows/backend-deploy.yml. Manual build:
#   cd apps/backend && docker build -f deploy/linkedin-login/Dockerfile -t <repo> .
#
# Keep the base Playwright version in step with Dockerfile.worker.
FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

# Virtual display + VNC + noVNC (websockify) so the user can see/drive the browser.
# DEBIAN_FRONTEND=noninteractive + preset TZ so tzdata (pulled in by x11vnc/noVNC)
# installs without the interactive "select your timezone" prompt that hangs CI.
ENV DEBIAN_FRONTEND=noninteractive TZ=Etc/UTC
RUN apt-get update && apt-get install -y --no-install-recommends \
      xvfb x11vnc novnc websockify curl ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
# Bake the Chromium matching the pip-installed Playwright into the image.
RUN python -m playwright install chromium

COPY . /app
RUN chmod +x /app/deploy/linkedin-login/entrypoint.sh

# noVNC live-view port (open this in the task's security group — see README).
EXPOSE 6080
ENV DISPLAY=:99 \
    LI_NOVNC_PORT=6080 \
    GTM_LINKEDIN_HEADLESS=false

ENTRYPOINT ["/app/deploy/linkedin-login/entrypoint.sh"]
