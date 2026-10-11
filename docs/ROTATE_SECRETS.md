# Rotate two secrets (do it once the new version has worked for a day)

Your admin login: **official.midotrix@gmail.com**. Tools needed: Git Bash and a browser — nothing to install.
`<…>` = a value you fill in.

## 1. The password of the account n8n logs in with
You need: the API address, your admin password, and 5 minutes. Git Bash, any folder:
1. ```bash
   API=https://<your-api>.onrender.com
   read -rsp "Your admin password: " PW; echo
   TOKEN=$(curl -s -X POST $API/auth/login -H 'Content-Type: application/json' -d "{\"email\":\"official.midotrix@gmail.com\",\"password\":\"$PW\"}" | sed -E 's/.*"token":"([^"]+)".*/\1/')
   ```
2. Find n8n's account — one user per line; copy the `id` of the n8n one:
   ```bash
   curl -s $API/users -H "Authorization: Bearer $TOKEN" | sed 's/},{/}\n{/g'
   ```
3. Make a strong password and show it once (you will paste it into n8n):
   ```bash
   NEWPW=$(openssl rand -base64 24); echo "$NEWPW"
   ```
4. Set it (replace `<ID>`; `204` means done):
   ```bash
   curl -s -o /dev/null -w "%{http_code}\n" -X POST $API/users/<ID>/password -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d "{\"new_password\":\"$NEWPW\"}"
   ```
   This immediately signs the n8n account out everywhere (its old password and old token stop working).
5. **n8n:** open the credential the ReconFlow workflow uses to log in (n8n → **Credentials**, or the login node of the workflow), replace the password with the new one, **Save**, then run the workflow once. *(I will tell you the exact node/credential name when I have your workflow JSON.)*
6. Check the old password is dead (want `401`): the same `curl` login as in step 1 using the old password for n8n's email.
7. Optional, same method: rotate your own admin password — `POST $API/users/<YOUR-ID>/password` with both `"current_password"` and `"new_password"`.

## 2. The database password on Render
Render's own method (docs: "Database Credential Rotations"): add a new database user, point the service at it, then delete the old one. Zero downtime.
1. Render → your Postgres → **Info** → **Credentials** → **+ New default credential** → **Create Credential**. The new user becomes the default and the connection URLs on the page now show it.
2. Render → **Connect → Internal** → copy the **Internal Database URL** (the web service lives in the same region, so use *internal*).
3. Render → your **web service → Environment** → the variable **`RECONFLOW_DATABASE_URL`** → paste the new URL → **Save changes**. Render redeploys the service. *(A plain `postgresql://…` URL is fine; the code selects the right driver.)*
4. Wait until the deploy is live, then test: open the frontend, log in, open an analysis.
5. **Check nothing still uses the old user.** In the Codespace/Git Bash terminal (you may need a new Codespace; the External URL is on the same Connect menu):
   ```bash
   psql '<External Database URL>' -c "SELECT COUNT(*) FROM pg_stat_activity WHERE usename = '<OLD USERNAME>';"
   ```
   The old username is the one in your *old* URL (`postgresql://<OLD USERNAME>:…`). Wait for `0`.
6. Render → Credentials → trash icon next to the **old** user → confirm. (Render deactivates the original user instead of erasing it — that is expected.)
7. **Anywhere else the old URL or password was pasted must be updated or deleted:** n8n (if your workflow connects to this database — tell me when I see the JSON), pgAdmin/other tools, notes, old Codespaces, old browser tabs.

---
