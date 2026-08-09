# Pipelyt Production Deployment Guide (AWS Lambda + Amplify)

This guide will help you set up and deploy Pipelyt to AWS using the dev/staging/main branches. No deep technical knowledge is required—just follow the steps below.

---

## 🏗️ Phase 1: AWS Infrastructure Setup

You need to create a few things in your AWS account first.

### 1. Amazon ECR (Backend Image Storage)
1. Go to **Amazon ECR** in the AWS Console.
2. Click **Create repository**.
3. Set the name to: `pipelyt-backend`.
4. Click **Create**.

### 2. AWS Lambda (Backend API)
1. Go to **AWS Lambda**.
2. Click **Create function**.
3. Choose **Container image**.
4. Set the name to: `pipelyt-backend`.
5. For **Container image URI**, browse and select any dummy image for now (or wait until you push your first image via GitHub).
6. **Enable Function URL & CORS (EXTREMELY IMPORTANT)**:
   - Once the function is created, go to the **Configuration** tab.
   - Click **Function URL** on the left menu.
   - Click **Edit**.
   - Select **NONE** for Auth Type.
   - **Check the box** for "Configure cross-origin resource sharing (CORS)".
   - Set **Allow origin** to `*`
   - Set **Allow methods** to `*`
   - Set **Allow headers** to `*`
   - Click **Save**.
7. **Copy your URL**: It will look like `https://...lambda-url.us-east-1.on.aws/`.

> [!WARNING]
> If you get a **403 Forbidden** error in your browser console, it means you missed step 6 above. FastAPI cannot fix this; you MUST do it in the AWS Console.

### 3. AWS Amplify (Frontend Hosting)
1. Go to **AWS Amplify**.
2. Click **New App** -> **Host web app**.
3. Choose **GitHub** and connect your repository.
4. Select your repo and the **main** branch.
5. Amplify will automatically detect the `amplify.yml` file I created.
6. Click **Save and Deploy**.

### 4. SPA Rewrite Rule (MANDATORY for both `landing-page` and `product-page` apps)

React Router uses client-side routing. Without this rewrite, direct visits or
page refreshes on routes like `/onboarding`, `/signup`, `/contact` return
**404 (Not Found)** because no file exists at those paths — only `/index.html`.

Apply this to **every Amplify app** (production AND staging) for **both**
frontends:

1. In the Amplify console, open the app (e.g. `pipelyt-product-page`).
2. Left sidebar → **Rewrites and redirects**.
3. Click **Open text editor** (JSON view).
4. Paste the contents of [`amplify-redirects.json`](../amplify-redirects.json)
   (at the repo root).
5. Click **Save**. No redeploy needed — takes effect immediately.

The rule says: any path that is NOT a static asset file (css/js/png/etc.) should
serve `/index.html` with HTTP 200. React Router then picks up the URL and
renders the correct page.

Verify by visiting `https://app.pipelyt.ai/onboarding` directly in a new tab —
it should render the onboarding UI instead of returning 404.

---

## 🔒 Phase 2: GitHub Secrets (The "Connection")

To let GitHub deploy to AWS automatically, you need to add "Keys" to your repository:

1. In your GitHub repo, go to **Settings** -> **Secrets and variables** -> **Actions**.
2. Click **New repository secret** for each of these:
   - `AWS_ACCESS_KEY_ID`: Your AWS Access Key.
   - `AWS_SECRET_ACCESS_KEY`: Your AWS Secret Key.
   - `AWS_REGION`: `us-east-1` (based on your current setup).

---

## 🔑 Phase 3: Environment Variables (The "Brains")

Both your frontend and backend need configuration to "talk" to each other.

### 1. Backend variables (Set in AWS Lambda)
Go to **AWS Lambda** -> **Configuration** -> **Environment variables**. Add these:
- `DATABASE_URL`: Your PostgreSQL connection string.
- `S3_BUCKET_NAME`: Your bucket name for image storage.
- `FRONTEND_URL`: The URL provided by AWS Amplify (e.g., `https://main.d123.amplifyapp.com`).
- `GEMINI_API_KEY`: Your Google AI key.
- `TWITTER_REDIRECT_URI`: `https://okhyiuosoj6pqpxxsvvb3pymy0lixsn.lambda-url.us-east-1.on.aws/auth/twitter/callback`
- `REDIRECT_URI`: `https://okhyiuosoj6pqpxxsvvb3pymy0lixsn.lambda-url.us-east-1.on.aws/auth/linkedin/callback`
- `FACEBOOK_REDIRECT_URI`: `https://okhyiuosoj6pqpxxsvvb3pymy0lixsn.lambda-url.us-east-1.on.aws/auth/facebook/callback`

### 2. Frontend variables (Set in AWS Amplify)
Go to **AWS Amplify** -> **App Settings** -> **Environment variables**. Add:
- `VITE_API_URL`: `https://okhyiuosoj6pqpxxsvvb3pymy0lixsn.lambda-url.us-east-1.on.aws`

---

## 🔗 Phase 4: Updating Your Social Apps (OAuth)

You must now update your Developer Portals with your actual production URL.

### 1. LinkedIn Developer Portal
1. Go to **Auth** -> **OAuth 2.0 settings**.
2. Add: `https://okhyiuosoj6pqpxxsvvb3pymy0lixsn.lambda-url.us-east-1.on.aws/auth/linkedin/callback`

### 2. Facebook/Instagram (Meta Developers)
1. Go to **App Settings** -> **Facebook Login** -> **Settings**.
2. Add: `https://okhyiuosoj6pqpxxsvvb3pymy0lixsn.lambda-url.us-east-1.on.aws/auth/facebook/callback`

### 3. Twitter (X) Developer Portal
1. Go to **User authentication settings**.
2. Add: `https://okhyiuosoj6pqpxxsvvb3pymy0lixsn.lambda-url.us-east-1.on.aws/auth/twitter/callback`
3. **Important**: Change app permissions to **"Read and Write"** to allow image posting.

---

Run these commands in your terminal to push your work to the **main** branch. This will trigger the automatic deployment.

### 1. Stage and Commit Changes
```bash
git add .
git commit -m "chore: prepare for production deployment"
```

### 2. Push to Main Branch
```bash
git checkout main
git push origin main
```

---

## 📝 Important Notes for Non-Technical Users

- **Backend Updates**: Every time you push code to `apps/backend/`, GitHub will automatically build a new "Container" and update your Lambda function.
- **Frontend Updates**: Every time you push code, AWS Amplify will automatically detect the change, build your React app, and refresh the website.
- **S3 Bucket**: Ensure your `.env` file in the Lambda function configuration has the correct `S3_BUCKET_NAME` for your images.

---

**You are now ready for production! 🚀**
If you see any "403" errors in GitHub Actions, double-check that your AWS User has `AmazonEC2ContainerRegistryFullAccess` and `AWSLambda_FullAccess` permissions.
