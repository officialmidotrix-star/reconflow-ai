# Batch 1.1 — apply the zip (Git Bash)
Folder for every line: the repo's top folder (the one that contains `apps`, `docs`, `tests`).
1. `git checkout batch1-v2`
2. `unzip -o ../reconflow-batch1-1.zip`   (use the real path of the zip)
3. `git add .gitignore .gitattributes DECISIONS.md PROJECT_STATUS.md apps docs scripts tests`
4. `git status`   (look: no `node_modules`, `.env`, `__pycache__`)
5. `git commit -m "Batch 1.1: migrate at deploy time, change password, list users, .gitignore"`
6. `git push origin batch1-v2`
7. Then follow `docs/CUTOVER_ON_RENDER.md` (clicks only).
