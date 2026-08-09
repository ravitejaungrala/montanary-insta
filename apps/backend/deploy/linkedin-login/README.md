# GTM LinkedIn — Cloud Login (Phase 2) deployment

The user logs into LinkedIn inside a browser **we host** (Fargate + noVNC). On
success the full browser profile is captured to the private S3 bucket
(`profile_store`) and the account activated. The password never reaches our backend.

## Naming convention
All resources are named `pipelyt-gtm-linkedin-<subname>`:
- ECR repo: `pipelyt-gtm-linkedin-login` (staging: `staging-pipelyt-gtm-linkedin-login`)
- ECS cluster: `pipelyt-gtm-linkedin-cluster`
- Task definition family: `pipelyt-gtm-linkedin-login`
- Container name: `pipelyt-gtm-linkedin-login`
- Security group: `pipelyt-gtm-linkedin-sg`
- Task role: `pipelyt-gtm-linkedin-task-role`
- Log group: `/ecs/pipelyt-gtm-linkedin-login`
- Profile bucket (already created): `pipelyt-gtm-linkedin-profiles`

## Pieces
- `Dockerfile` + `entrypoint.sh` — the hosted browser image (Chromium + Xvfb + x11vnc + noVNC).
- `gtm/linkedin/fargate_login_task.py` — container entrypoint: publishes viewer URL, runs login, saves profile.
- API: `POST /nexus/linkedin-agent/connect/cloud/start`, `GET /nexus/linkedin-agent/connect/cloud/{session_id}`.
- Frontend: `NexusLinkedInCloudConnect.jsx` (3rd "Secure browser" mode in the connect modal).

## 1. Image build & push — AUTOMATED via GitHub Actions
`.github/workflows/backend-deploy.yml` builds the image and pushes it on every push
to `main`/`staging` that touches `apps/backend/**`. It **auto-creates** the ECR repo
(`pipelyt-gtm-linkedin-login`) and pushes `:sha` + `:latest`. No manual `docker build`.
Uses the existing GitHub secrets `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_REGION`.

## 2. ECS task definition (Fargate) — one-time in the console
- Container named **`pipelyt-gtm-linkedin-login`**, image `…/pipelyt-gtm-linkedin-login:latest`, port **6080**.
- Env: `DATABASE_URL`, `LINKEDIN_COOKIE_KEY`, `GTM_LINKEDIN_PROFILE_BUCKET=pipelyt-gtm-linkedin-profiles`, `AWS_REGION`.
  (`LI_ACCOUNT_ID` + `LI_SESSION_ID` are injected per run by the API.)
- Task role `pipelyt-gtm-linkedin-task-role`: `s3:GetObject/PutObject/DeleteObject` on
  `arn:aws:s3:::pipelyt-gtm-linkedin-profiles/linkedin-profiles/*`.
- Execution role `ecsTaskExecutionRole`. CPU/mem 1 vCPU / 2 GB. Log group `/ecs/pipelyt-gtm-linkedin-login`.

## 3. Networking
- Run tasks with **`assignPublicIp=ENABLED`** (the API sets this).
- Security group `pipelyt-gtm-linkedin-sg`: inbound **TCP 6080 from the user's IP** (not 0.0.0.0).
- Add an inbound rule on the **RDS** security group: PostgreSQL 5432 from `pipelyt-gtm-linkedin-sg` (so the task can write to the DB).

## 4. Backend env (so the API can launch tasks)
```
LI_LOGIN_ECS_CLUSTER=pipelyt-gtm-linkedin-cluster
LI_LOGIN_ECS_TASKDEF=pipelyt-gtm-linkedin-login
LI_LOGIN_ECS_SUBNETS=subnet-aaa,subnet-bbb        # public subnets in the RDS VPC
LI_LOGIN_ECS_SECURITY_GROUPS=sg-xxxx               # pipelyt-gtm-linkedin-sg
LI_LOGIN_ECS_CONTAINER=pipelyt-gtm-linkedin-login
```
Backend role needs `ecs:RunTask` + `iam:PassRole` (task + execution roles).
**Unset `LI_LOGIN_ALLOW_LOCAL` in production.** Without ECS env, `/connect/cloud/start` returns 503.

## 5. Frontend
`<LinkedInCloudConnect/>` is already wired as the "Secure browser" mode in `LinkedInConnectModal`.

## Flow
```
UI "Connect via secure browser" → POST /connect/cloud/start → account + login-session, ECS runTask
  → task: Xvfb + x11vnc + noVNC up → writes viewer_url to the session row
  → UI polls GET /connect/cloud/{id} → shows viewer_url in an iframe
  → user logs in (password + MFA) inside that browser
  → task detects login → saves full profile to S3 → session 'saved', account active
  → automation worker reuses the profile — already logged in.
```

## Hardening TODO (before production)
- Restrict noVNC to the user's IP (per-session SG rule) or an authenticated proxy + TLS.
- Set `LI_VNC_PASSWORD` and surface it to the user, or use a one-time token.
- The task exits when login completes; also set a task-level timeout.
