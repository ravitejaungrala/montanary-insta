# How to Connect Instagram to Pipelyt (Easy Setup Guide)

Connecting your Instagram account to Pipelyt requires using Facebook's official developer platform. Even if you're not technical, you can easily set this up by following these step-by-step instructions.

## Prerequisites
Before you start, make sure you have:
1. **A Facebook Account:** A standard personal Facebook account.
2. **A Facebook Page:** You must be the admin of a Facebook Business Page.
3. **An Instagram Professional Account:** Your Instagram account must be set to "Business" or "Creator" mode.
4. **Linked Accounts:** Your Instagram account must be linked to your Facebook Business Page. (You can do this inside the Instagram app settings under "Accounts Center" or "Linked Accounts").

---

## Step 1: Create a Meta Developer Account
1. Go to [developers.facebook.com](https://developers.facebook.com/) and click **Log In** in the top right corner. Use your normal Facebook login.
2. Once logged in, click **"Get Started"** or **"My Apps"** in the top menu.
3. If this is your first time, follow the quick registration prompts to verify your account (you may need to enter a phone number).

## Step 2: Create a New App
1. In the "My Apps" dashboard, click the green **"Create App"** button.
2. When asked what your app does, select **"Other"** and click Next.
3. Select **"Business"** as the app type and click Next.
4. Give your app a name (e.g., "Pipelyt Automations") and enter your email address.
5. Provide your Facebook password when prompted to finish creating the app.

## Step 3: Add the Required Products
Even if you only want to post to Facebook, you must tell Facebook your app is allowed to talk to Instagram-linked pages!
1. On your App Dashboard, scroll down to "Add products to your app" and look for **Facebook Login for Business**. Click **"Set Up"**.
2. Go back to the Dashboard or click "Add Product" in the left sidebar again.
3. Find **Instagram Graph API** and click **"Set Up"**. (You don't need to configure much here, just having it "Active" in your sidebar is the key to making Facebook pages visible again).

## Step 4: Configure Facebook Login Settings
1. In the left-hand sidebar menu, under "Facebook Login for Business", click **"Settings"**.
2. Look for the field labeled **Valid OAuth Redirect URIs**.
3. Type in the following EXACT URL and hit Enter:
   `http://localhost:8000/auth/facebook/callback`
   *(Note: This URL tells Facebook where to send you after a successful login).*
4. Click **Save Changes** at the bottom right.

## Step 5: Get Your Secret Keys
To connect the app to your code, you need two secret codes.
1. In the left-hand sidebar, click **"App Settings"** > **"Basic"**.
2. Here you will see your **App ID** and **App Secret**. (Click "Show" to reveal the secret).
3. Copy both of these.

## Step 6: Put the Keys into Pipelyt
1. Open your Pipelyt files and find the file named `.env` in the `apps/backend` folder.
2. Find the lines for Facebook and Instagram and paste your keys:
   ```text
   FACEBOOK_APP_ID=paste_your_app_id_here
   FACEBOOK_APP_SECRET=paste_your_app_secret_here
   ```
3. Save the file and restart your Pipelyt server!

## Step 7: Connect from the Dashboard
1. Go to your Pipelyt Dashboard at `http://localhost:5173`.
2. Click the **"Connections"** tab.
3. Click **Connect** under Facebook/Instagram.
4. A Facebook login window will pop up. Follow the prompts to select your Facebook Page and your linked Instagram Professional account.
5. Click **OK**, and your accounts will now appear in Pipelyt!

---
**Troubleshooting Tip:** If you see an error saying "The Redirect URI is not valid," double-check Step 4 and make sure you saved the exact URL without any typos or extra spaces.
