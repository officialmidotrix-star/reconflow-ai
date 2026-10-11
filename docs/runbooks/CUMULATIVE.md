# Apply the cumulative zip (Git Bash) — Batches 1.1 + 2 + 3 in one go
Folder for every line: the repo's top folder (the one that contains `apps`, `docs`, `tests`). Your `batch1-v2` branch must already be pushed.
1. `git checkout batch1-v2 && git checkout -b batch3-cumulative`
2. `unzip -o ../reconflow-batch3-cumulative.zip`   (use the real path of the zip)
3. `git add .gitignore .gitattributes DECISIONS.md PROJECT_STATUS.md apps docs scripts tests tools`
4. `git status`   (look: no `node_modules`, `.env`, `__pycache__`)
5. `git commit -m "Batches 1.1, 2 and 3: deploy-time migration, numbers, speed, S3 storage, test data"`
6. `git push -u origin batch3-cumulative`
7. Test deploy: follow `docs/CUTOVER_ON_RENDER.md` (clicks only; it tells you to merge `batch3-cumulative`).
Later zips (4, 5, 6) work the same way: new branch from `batch1-v2`, unzip, add, commit, push.
