# Batch 2 (the numbers) — apply the zip (Git Bash)
Folder for every line: the repo's top folder. Do Batch 1.1 first.
1. `git checkout batch1-v2 && git checkout -b batch2-numbers`
2. `unzip -o ../reconflow-batch2.zip`   (use the real path)
3. `git add DECISIONS.md apps docs tests`
4. `git status`   (nothing unexpected?)
5. `git commit -m "Batch 2: headline = recoverable underpayments net of commission"`
6. `git push -u origin batch2-numbers`
7. GitHub → Pull requests → New → base `main`, compare `batch2-numbers` → Merge. Render redeploys and **migrates the database by itself** (you will see "Migration complete: database is at 0003" in the Logs).
