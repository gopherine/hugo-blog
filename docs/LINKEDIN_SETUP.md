# LinkedIn API Setup for Auto-Syndication

## One-Time Setup

1. Go to https://www.linkedin.com/developers/apps and create a new app
2. Under "Products", request access to "Share on LinkedIn"
3. Under "Auth" tab, add redirect URL: `https://localhost:3000/callback`
4. Note your Client ID and Client Secret

## Get Access Token

1. Generate OAuth URL:
   ```
   https://www.linkedin.com/oauth/v2/authorization?response_type=code&client_id=YOUR_CLIENT_ID&redirect_uri=https://localhost:3000/callback&scope=w_member_social%20openid%20profile
   ```
2. Visit URL, authorize, copy the `code` param from redirect
3. Exchange for token:
   ```bash
   curl -X POST https://www.linkedin.com/oauth/v2/accessToken \
     -d "grant_type=authorization_code&code=YOUR_CODE&redirect_uri=https://localhost:3000/callback&client_id=YOUR_CLIENT_ID&client_secret=YOUR_CLIENT_SECRET"
   ```
4. Save the `access_token` from the response

## Get Your Person ID

```bash
curl -H "Authorization: Bearer YOUR_TOKEN" https://api.linkedin.com/v2/userinfo
```

The `sub` field is your person ID.

## Store as GitHub Secrets

In your repo settings → Secrets → Actions:
- `LINKEDIN_ACCESS_TOKEN` — the access token
- `LINKEDIN_PERSON_ID` — your person ID

## Token Refresh

LinkedIn tokens expire after 60 days. Regenerate by repeating the OAuth flow.
